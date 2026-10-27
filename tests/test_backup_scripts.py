"""Скрипты резервного копирования deploy/backup.py и deploy/restore_check.py (п. 4.13).

Проверяется то, что работает без облака и без Docker: имена копий, разбор
аргументов, чтение .env, команды rclone (ключи не попадают в командную строку),
выбор последней копии, сравнение баз и отчёт.
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))

import backup  # noqa: E402
import restore_check  # noqa: E402

SECRET = "very-secret-value"


def test_dump_name_has_date_and_time():
    assert backup.dump_name(datetime(2026, 10, 27, 4, 0)) == "teamplan-2026-10-27-0400.dump"
    assert backup.dump_name(datetime(2026, 1, 5, 23, 7), "predeploy") == (
        "teamplan-2026-01-05-2307-predeploy.dump"
    )


@pytest.mark.parametrize("name, ok", [
    ("teamplan-2026-10-27-0400.dump", True),
    ("teamplan-2026-10-27-0400-predeploy.dump", True),
    ("teamplan-2026-10-27.dump", False),
    ("teamplan-2026-10-27-0400.sql", False),
    ("other-2026-10-27-0400.dump", False),
    ("../teamplan-2026-10-27-0400.dump", False),
    ("teamplan-2026-10-27-0400-Pre Deploy.dump", False),
])
def test_is_dump_name(name, ok):
    assert backup.is_dump_name(name) is ok


def test_created_names_are_recognized():
    assert backup.is_dump_name(backup.dump_name(datetime(2026, 10, 27, 4, 0), "before-restore"))


def test_parser_create_and_label():
    parser = backup.build_parser()
    assert parser.parse_args(["create"]).label is None
    assert parser.parse_args(["create", "--label", "predeploy"]).label == "predeploy"
    assert parser.parse_args(["list"]).command == "list"
    args = parser.parse_args(["download", "teamplan-2026-10-27-0400.dump", "--to", "/tmp/x"])
    assert (args.name, args.to) == ("teamplan-2026-10-27-0400.dump", "/tmp/x")


@pytest.mark.parametrize("argv", [
    [],
    ["delete"],
    ["create", "--label", "Pre Deploy"],
    ["create", "--label", "../x"],
    ["create", "--label", "a" * 41],
    ["download", "secret.txt"],
])
def test_parser_rejects_bad_arguments(argv):
    with pytest.raises(SystemExit) as exc:
        backup.build_parser().parse_args(argv)
    assert exc.value.code == 2


def test_restore_check_arguments():
    parser = restore_check.build_parser()
    assert parser.parse_args([]).name is None
    assert parser.parse_args(["teamplan-2026-10-27-0400.dump"]).name == "teamplan-2026-10-27-0400.dump"
    with pytest.raises(SystemExit):
        parser.parse_args(["teamplan.dump"])


def test_read_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# комментарий\n"
        "TZ=Asia/Irkutsk\n"
        "BACKUP_S3_KEY_ID=key-id\n"
        f"BACKUP_S3_SECRET='{SECRET}'\n"
        'export BACKUP_S3_BUCKET="teamplan-backups-irk"\n'
        "\n"
        "BROKEN LINE\n",
        encoding="utf-8",
    )
    values = backup.read_env_file(env)
    assert values["TZ"] == "Asia/Irkutsk"
    assert values["BACKUP_S3_SECRET"] == SECRET
    assert values["BACKUP_S3_BUCKET"] == "teamplan-backups-irk"
    assert "BROKEN LINE" not in values


def test_load_settings_reports_missing_variables_without_values(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"BACKUP_S3_KEY_ID=key-id\nBACKUP_S3_SECRET={SECRET}\n", encoding="utf-8")
    with pytest.raises(backup.BackupError) as exc:
        backup.load_settings(env)
    assert "BACKUP_S3_BUCKET" in str(exc.value)
    assert SECRET not in str(exc.value)


def test_load_settings_without_env_file(tmp_path):
    with pytest.raises(backup.BackupError, match="не найден"):
        backup.load_settings(tmp_path / ".env")


def settings():
    return {
        "BACKUP_S3_KEY_ID": "key-id",
        "BACKUP_S3_SECRET": SECRET,
        "BACKUP_S3_BUCKET": "teamplan-backups-irk",
    }


def test_rclone_command_passes_secrets_only_by_name():
    env = backup.rclone_env(settings())
    assert env["RCLONE_CONFIG_YC_SECRET_ACCESS_KEY"] == SECRET
    assert env["RCLONE_CONFIG_YC_ENDPOINT"] == "https://storage.yandexcloud.net"
    assert env["RCLONE_CONFIG_YC_REGION"] == "ru-central1"
    cmd = backup.rclone_command(env, ["lsjson", "yc:teamplan-backups-irk/"])
    assert SECRET not in " ".join(cmd)
    assert "key-id" not in " ".join(cmd)
    assert "RCLONE_CONFIG_YC_SECRET_ACCESS_KEY" in cmd
    assert backup.RCLONE_IMAGE in cmd


def test_rclone_image_version_is_pinned():
    name, _, tag = backup.RCLONE_IMAGE.partition(":")
    assert name == "rclone/rclone"
    assert tag and tag != "latest" and tag[0].isdigit()


def test_scripts_never_delete_from_bucket():
    """У сервисного аккаунта нет права удалять: скрипты не вызывают удаляющие команды rclone."""
    root = Path(backup.__file__).parent
    for script in ("backup.py", "restore_check.py"):
        text = (root / script).read_text(encoding="utf-8")
        for command in ('"delete"', '"deletefile"', '"purge"', '"sync"', '"move"', '"moveto"',
                        '"cleanup"', '"rmdir"', '"rmdirs"'):
            assert command not in text, f"{script}: {command}"


def test_remote_path():
    assert backup.remote_path(settings(), "x.dump") == "yc:teamplan-backups-irk/x.dump"


def test_sort_dumps_picks_latest_and_skips_other_files():
    entries = [
        {"Path": "teamplan-2026-10-28-0400.dump", "Size": 10, "ModTime": "2026-10-27T20:00:05Z"},
        {"Path": "notes.txt", "Size": 1, "ModTime": "2026-10-29T00:00:00Z"},
        {"Path": "teamplan-2026-10-27-0400.dump", "Size": 9, "ModTime": "2026-10-26T20:00:04Z"},
    ]
    dumps = backup.sort_dumps(entries)
    assert [d["name"] for d in dumps] == [
        "teamplan-2026-10-27-0400.dump", "teamplan-2026-10-28-0400.dump",
    ]


def test_compose_files(monkeypatch):
    monkeypatch.delenv("TEAMPLAN_COMPOSE_FILES", raising=False)
    assert backup.compose_command("ps") == [
        "docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.prod.yml", "ps",
    ]
    monkeypatch.setenv("TEAMPLAN_COMPOSE_FILES", "docker-compose.yml")
    assert backup.compose_command("ps") == ["docker", "compose", "-f", "docker-compose.yml", "ps"]


def test_server_now_uses_tz_from_env():
    now = backup.server_now({"TZ": "Asia/Irkutsk"})
    assert now.utcoffset().total_seconds() == 8 * 3600
    with pytest.raises(backup.BackupError, match="часовой пояс"):
        backup.server_now({"TZ": "Nowhere/City"})


def test_human_size():
    assert backup.human_size(512) == "512 Б"
    assert backup.human_size(2048) == "2.0 КБ"
    assert backup.human_size(3 * 1024 * 1024) == "3.0 МБ"


def results(users="3", stats="1|A|7|2|1|0\n2|B||2|0|1"):
    return {
        "Пользователи": users, "Игроки": "2", "События": "5", "Голы": "1",
        "Статистика игроков (строк)": stats,
    }


def test_compare_equal():
    rows, ok = restore_check.compare(results(), results())
    assert ok
    report = restore_check.format_report(rows, ok)
    assert report.endswith("Итог: совпадает")
    # Имена игроков в журнал не выводятся.
    assert "|A|" not in report and "|B|" not in report


def test_compare_detects_difference():
    rows, ok = restore_check.compare(results(), results(users="4"))
    assert not ok
    assert ("Пользователи", "3", "4", False) in rows
    assert "Итог: не совпадает" in restore_check.format_report(rows, ok)


def test_compare_detects_stats_difference_with_same_row_count():
    rows, ok = restore_check.compare(results(), results(stats="1|A|7|2|1|0\n2|B||2|0|0"))
    assert not ok
    assert rows[-1] == ("Статистика игроков (строк)", "2", "2", False)


def test_check_database_is_separate():
    assert restore_check.CHECK_DB == "teamplan_restore_check"
    assert restore_check.CHECK_DB not in ("teamplan", "postgres")
