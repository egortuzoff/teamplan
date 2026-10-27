#!/usr/bin/env python3
"""Проверка восстановления из резервной копии (ТЗ, РК-4, AC-24).

Запускается на сервере из папки проекта:

    python3 deploy/restore_check.py                       последняя копия в бакете
    python3 deploy/restore_check.py teamplan-2026-10-27-0400.dump   указанная копия

Скачивает копию, разворачивает её в отдельную пустую базу teamplan_restore_check
(рабочую базу не изменяет), сравнивает с рабочей базой число пользователей,
игроков, событий, голов и таблицу статистики игроков, печатает «совпадает» или
«не совпадает» и удаляет проверочную базу и скачанный файл. Имена игроков
в журнал не выводятся.

Сравнение имеет смысл, пока данные в рабочей базе не менялись после создания
копии: удобнее всего запускать сразу после deploy/backup.py create.

Код выхода: 0 — совпадает, 1 — не совпадает, 2 — ошибка.
"""
import argparse
import shutil
import subprocess
import sys
import tempfile

import backup
from backup import BackupError, compose_command, log

CHECK_DB = "teamplan_restore_check"

# Что сравнивается: подпись и запрос. Статистика — как в приложении
# (teamplan/results.py, player_stats): игры с внесённым счётом в составе, голы, передачи.
CHECKS = [
    ("Пользователи", "SELECT count(*) FROM users"),
    ("Игроки", "SELECT count(*) FROM players"),
    ("События", "SELECT count(*) FROM events"),
    ("Голы", "SELECT count(*) FROM goals"),
    ("Статистика игроков (строк)", """
        SELECT p.id, p.name, coalesce(p.number::text, ''),
               (SELECT count(*) FROM squad_players sp JOIN results r ON r.event_id = sp.event_id
                 WHERE sp.player_id = p.id),
               (SELECT count(*) FROM goals g WHERE g.scorer_id = p.id),
               (SELECT count(*) FROM goals g WHERE g.assist_id = p.id)
          FROM players p ORDER BY p.id
    """),
]


def db_shell(script, *args, stdin=None):
    """Команда sh в контейнере db; пользователь базы — из переменных контейнера."""
    result = subprocess.run(
        compose_command("exec", "-T", "db", "sh", "-c", script, "sh", *args),
        cwd=backup.PROJECT_DIR, stdin=stdin, stdout=subprocess.PIPE, text=True,
    )
    if result.returncode != 0:
        raise BackupError(f"команда в контейнере db завершилась с кодом {result.returncode}")
    return result.stdout


def working_db_name():
    name = db_shell('printf %s "$POSTGRES_DB"').strip()
    if not name:
        raise BackupError("не удалось узнать имя рабочей базы (POSTGRES_DB в контейнере db)")
    if name == CHECK_DB:
        raise BackupError(f"рабочая база называется {CHECK_DB} — проверка остановлена")
    return name


def query(dbname, sql):
    return db_shell(
        'psql -U "$POSTGRES_USER" -d "$1" -X -A -t -q -v ON_ERROR_STOP=1 -c "$2"', dbname, sql,
    ).strip()


def collect(dbname):
    """Результаты запросов CHECKS для базы dbname: {подпись: текст результата}."""
    return {label: query(dbname, sql) for label, sql in CHECKS}


def compare(working, restored):
    """Строки отчёта (подпись, в рабочей, в копии, совпадает) и общий итог."""
    rows = []
    for label, _ in CHECKS:
        a, b = working[label], restored[label]
        if label.startswith("Статистика"):
            # Содержимое таблицы (имена) не выводится — только число строк.
            shown_a = str(len(a.splitlines())) if a else "0"
            shown_b = str(len(b.splitlines())) if b else "0"
        else:
            shown_a, shown_b = a, b
        rows.append((label, shown_a, shown_b, a == b))
    return rows, all(ok for *_, ok in rows)


def format_report(rows, ok):
    width = max(len(label) for label, *_ in rows)
    lines = [f"{'':{width}}  {'рабочая':>8}  {'копия':>8}"]
    for label, a, b, same in rows:
        lines.append(f"{label:{width}}  {a:>8}  {b:>8}  {'совпадает' if same else 'НЕ СОВПАДАЕТ'}")
    lines.append("Итог: " + ("совпадает" if ok else "не совпадает"))
    return "\n".join(lines)


def restore_into_check_db(path):
    log(f"Восстановление в отдельную базу {CHECK_DB}…")
    with open(path, "rb") as dump:
        db_shell(
            'set -e; dropdb -U "$POSTGRES_USER" --if-exists "$1"; '
            'createdb -U "$POSTGRES_USER" -T template0 "$1"; '
            'pg_restore -U "$POSTGRES_USER" -d "$1" --no-owner --exit-on-error',
            CHECK_DB, stdin=dump,
        )


def drop_check_db():
    db_shell('dropdb -U "$POSTGRES_USER" --if-exists "$1"', CHECK_DB)
    log(f"Проверочная база {CHECK_DB} удалена.")


def run(settings, name=None):
    working = working_db_name()
    name = name or backup.latest_dump(settings)
    log(f"Проверка восстановления копии {name} (рабочая база {working} не изменяется).")
    tmpdir = tempfile.mkdtemp(prefix="teamplan-restore-check-")
    try:
        path = backup.download(settings, name, tmpdir)
        try:
            restore_into_check_db(path)
            rows, ok = compare(collect(working), collect(CHECK_DB))
        finally:
            # Проверочная база удаляется и при ошибке восстановления.
            drop_check_db()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    log(format_report(rows, ok))
    return ok


def build_parser():
    parser = argparse.ArgumentParser(
        prog="deploy/restore_check.py",
        description="Проверка восстановления копии в отдельную базу teamplan_restore_check.",
    )
    parser.add_argument(
        "name", nargs="?", type=backup.dump_name_type,
        help="имя копии (по умолчанию — последняя в бакете)",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        ok = run(backup.load_settings(), args.name)
    except BackupError as exc:
        print(f"ОШИБКА проверки восстановления: {exc}", file=sys.stderr, flush=True)
        return 2
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
