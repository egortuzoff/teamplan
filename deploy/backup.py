#!/usr/bin/env python3
"""Резервное копирование базы TeamPlan в Yandex Object Storage (ТЗ, п. 4.13).

Запускается на сервере из папки проекта (нужны Docker и Python 3.12+, только
стандартная библиотека):

    python3 deploy/backup.py create                  копия базы в бакет (РК-1, РК-2)
    python3 deploy/backup.py create --label predeploy  то же с пометкой в имени (РК-3)
    python3 deploy/backup.py list                    список копий в бакете
    python3 deploy/backup.py download ИМЯ            скачать копию (для восстановления)

Копия — pg_dump в формате custom из контейнера db. Загрузка — rclone в разовом
контейнере (официальный образ, версия зафиксирована). Ключи доступа читаются
из .env и передаются rclone только через переменные окружения: в командной
строке и в журнале их нет. Временный файл удаляется после загрузки.
Скрипт ничего не удаляет из бакета: старые копии удаляет правило жизненного
цикла бакета (14 дней).

Код выхода: 0 — успех, 1 — ошибка (сообщение в stderr, на сервере — в journalctl).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_DIR / ".env"

RCLONE_IMAGE = "rclone/rclone:1.71.2"
S3_ENDPOINT = "https://storage.yandexcloud.net"
S3_REGION = "ru-central1"
REMOTE = "yc"  # имя хранилища для rclone, задаётся переменными RCLONE_CONFIG_YC_*

# Файлы Compose рабочего режима. Для проверки на своём компьютере можно задать
# другие через переменную TEAMPLAN_COMPOSE_FILES (через запятую).
DEFAULT_COMPOSE_FILES = "docker-compose.yml,docker-compose.prod.yml"

NAME_PREFIX = "teamplan-"
NAME_SUFFIX = ".dump"
NAME_RE = re.compile(r"^teamplan-\d{4}-\d{2}-\d{2}-\d{4}(-[a-z0-9-]+)?\.dump$")
LABEL_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
REQUIRED_VARS = ("BACKUP_S3_KEY_ID", "BACKUP_S3_SECRET", "BACKUP_S3_BUCKET")


class BackupError(Exception):
    """Понятное сообщение об ошибке для журнала."""


def log(message):
    print(message, flush=True)


def dump_name(now, label=None):
    """Имя копии: teamplan-ГГГГ-ММ-ДД-ЧЧММ[-пометка].dump (время сервера)."""
    name = NAME_PREFIX + now.strftime("%Y-%m-%d-%H%M")
    if label:
        name += "-" + label
    return name + NAME_SUFFIX


def is_dump_name(name):
    return bool(NAME_RE.match(name))


def label_type(value):
    """Пометка в имени копии: строчные латинские буквы, цифры и дефисы."""
    if not LABEL_RE.match(value) or len(value) > 40:
        raise argparse.ArgumentTypeError(
            "пометка — строчные латинские буквы и цифры, слова через дефис, до 40 символов"
        )
    return value


def dump_name_type(value):
    if not is_dump_name(value):
        raise argparse.ArgumentTypeError(
            "имя копии вида teamplan-2026-10-27-0400.dump (список: deploy/backup.py list)"
        )
    return value


def read_env_file(path):
    """Читает KEY=VALUE из .env (как Docker Compose: без подстановок, кавычки снимаются)."""
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def load_settings(env_file=ENV_FILE):
    """Настройки копирования из .env. Значения ключей не выводятся."""
    if not Path(env_file).is_file():
        raise BackupError(f"не найден файл настроек {env_file} (образец — .env.example)")
    values = read_env_file(env_file)
    missing = [name for name in REQUIRED_VARS if not values.get(name)]
    if missing:
        raise BackupError(
            "в .env не заданы " + ", ".join(missing) + " — см. README, раздел "
            "«Резервное копирование и восстановление»"
        )
    return values


def rclone_env(settings):
    """Переменные окружения rclone: хранилище yc (S3-совместимое, Yandex Object Storage)."""
    prefix = "RCLONE_CONFIG_" + REMOTE.upper() + "_"
    return {
        prefix + "TYPE": "s3",
        prefix + "PROVIDER": "Other",
        prefix + "ENDPOINT": S3_ENDPOINT,
        prefix + "REGION": S3_REGION,
        prefix + "ACCESS_KEY_ID": settings["BACKUP_S3_KEY_ID"],
        prefix + "SECRET_ACCESS_KEY": settings["BACKUP_S3_SECRET"],
        # У сервисного аккаунта нет права создавать бакет: не проверять и не создавать.
        prefix + "NO_CHECK_BUCKET": "true",
        # Настройки только из переменных, файл настроек rclone не нужен.
        "RCLONE_CONFIG": "/dev/null",
    }


def rclone_command(env, args, mount=None):
    """docker run для rclone. Значения переменных в командную строку не попадают (-e ИМЯ)."""
    cmd = ["docker", "run", "--rm"]
    if hasattr(os, "getuid"):
        # Скачанный файл принадлежит текущему пользователю и удаляется без sudo.
        cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]
    for name in sorted(env):
        cmd += ["-e", name]
    if mount:
        cmd += ["-v", f"{mount}:/data"]
    return cmd + [RCLONE_IMAGE] + list(args)


def run_rclone(settings, args, mount=None, capture=False):
    env = rclone_env(settings)
    cmd = rclone_command(env, args, mount)
    result = subprocess.run(
        cmd, env={**os.environ, **env}, text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    if result.returncode != 0:
        raise BackupError(f"rclone {args[0]} завершился с кодом {result.returncode}")
    return result.stdout


def remote_path(settings, name=""):
    return f"{REMOTE}:{settings['BACKUP_S3_BUCKET']}/{name}"


def compose_command(*args):
    files = os.environ.get("TEAMPLAN_COMPOSE_FILES", DEFAULT_COMPOSE_FILES)
    cmd = ["docker", "compose"]
    for name in files.split(","):
        if name.strip():
            cmd += ["-f", name.strip()]
    return cmd + list(args)


def server_now(settings):
    """Текущее время в часовом поясе приложения (TZ из .env), иначе — системное."""
    tz = settings.get("TZ")
    if tz:
        try:
            return datetime.now(ZoneInfo(tz))
        except (ZoneInfoNotFoundError, ValueError):
            raise BackupError(f"в .env неизвестный часовой пояс TZ={tz}")
    return datetime.now()


def list_dumps(settings):
    """Копии в бакете: список словарей name, size, modified, от старых к новым."""
    out = run_rclone(settings, ["lsjson", "--files-only", remote_path(settings)], capture=True)
    return sort_dumps(json.loads(out or "[]"))


def sort_dumps(entries):
    dumps = [
        {"name": e["Path"], "size": e.get("Size", 0), "modified": e.get("ModTime", "")}
        for e in entries if is_dump_name(e.get("Path", ""))
    ]
    return sorted(dumps, key=lambda d: (d["modified"], d["name"]))


def human_size(size):
    for unit in ("Б", "КБ", "МБ"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ГБ"


def create(settings, label=None):
    name = dump_name(server_now(settings), label)
    tmpdir = tempfile.mkdtemp(prefix="teamplan-backup-")
    try:
        path = Path(tmpdir) / name
        log(f"Копия базы: {name}")
        with open(path, "wb") as out:
            result = subprocess.run(
                compose_command(
                    "exec", "-T", "db", "sh", "-c",
                    'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom',
                ),
                cwd=PROJECT_DIR, stdout=out,
            )
        if result.returncode != 0:
            raise BackupError(f"pg_dump завершился с кодом {result.returncode} (запущен ли контейнер db?)")
        size = path.stat().st_size
        if size == 0:
            raise BackupError("pg_dump создал пустой файл")
        log(f"Размер: {human_size(size)}. Загрузка в бакет {settings['BACKUP_S3_BUCKET']}…")
        run_rclone(settings, ["copyto", f"/data/{name}", remote_path(settings, name)], mount=tmpdir)
        log(f"Готово: {name} загружена в Object Storage.")
        return name
    finally:
        # Временный файл удаляется всегда: после успешной загрузки он не нужен,
        # а неполную копию хранить на сервере бессмысленно.
        shutil.rmtree(tmpdir, ignore_errors=True)


def latest_dump(settings):
    dumps = list_dumps(settings)
    if not dumps:
        raise BackupError(f"в бакете {settings['BACKUP_S3_BUCKET']} нет копий")
    return dumps[-1]["name"]


def download(settings, name, dest_dir):
    """Скачивает копию name в папку dest_dir, возвращает путь к файлу."""
    dest_dir = Path(dest_dir).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    log(f"Скачивание {name} из бакета {settings['BACKUP_S3_BUCKET']}…")
    run_rclone(settings, ["copyto", remote_path(settings, name), f"/data/{name}"], mount=dest_dir)
    path = dest_dir / name
    if not path.is_file() or path.stat().st_size == 0:
        raise BackupError(f"копия {name} не скачалась")
    log(f"Скачано: {path} ({human_size(path.stat().st_size)})")
    return path


def show_list(settings):
    dumps = list_dumps(settings)
    if not dumps:
        log("В бакете нет копий.")
        return
    for d in dumps:
        log(f"{d['name']}  {human_size(d['size']):>10}  {d['modified'][:19].replace('T', ' ')} UTC")
    log(f"Всего копий: {len(dumps)}")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="deploy/backup.py",
        description="Резервные копии базы TeamPlan в Yandex Object Storage.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_create = sub.add_parser("create", help="создать копию базы и загрузить в бакет")
    p_create.add_argument(
        "--label", type=label_type,
        help="пометка в имени копии, например predeploy (копия перед обновлением)",
    )
    sub.add_parser("list", help="список копий в бакете")
    p_download = sub.add_parser("download", help="скачать копию (для восстановления базы)")
    p_download.add_argument("name", type=dump_name_type, help="имя копии из списка list")
    p_download.add_argument(
        "--to", default=str(Path.home()), help="папка для файла (по умолчанию домашняя)",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings()
        if args.command == "create":
            create(settings, args.label)
        elif args.command == "download":
            download(settings, args.name, args.to)
        else:
            show_list(settings)
    except BackupError as exc:
        print(f"ОШИБКА резервного копирования: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
