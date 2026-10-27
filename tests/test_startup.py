"""Подготовка базы при запуске контейнера по режиму TEAMPLAN_MODE (ТР-6, РЗ-7)."""
import pytest

import seed
import startup
from teamplan import auth
from teamplan.settings import get_mode


def users(conn):
    return [r["login"] for r in conn.execute("SELECT login FROM users ORDER BY id")]


def test_mode_is_demo_by_default(monkeypatch):
    monkeypatch.delenv("TEAMPLAN_MODE", raising=False)
    assert get_mode() == "demo"
    monkeypatch.setenv("TEAMPLAN_MODE", " Production ")
    assert get_mode() == "production"


def test_unknown_mode_is_an_error(monkeypatch, capsys, conn):
    monkeypatch.setenv("TEAMPLAN_MODE", "prod")
    assert startup.main() == 1
    assert "TEAMPLAN_MODE" in capsys.readouterr().out
    assert users(conn) == []


def test_demo_mode_creates_test_data(conn):
    assert startup.main() == 0
    assert seed.ORGANIZER["login"] in users(conn)


def test_production_mode_creates_no_test_data_and_hints(monkeypatch, capsys, conn):
    monkeypatch.setenv("TEAMPLAN_MODE", "production")
    assert startup.main() == 0
    out = capsys.readouterr().out
    assert "create_organizer.py" in out
    assert users(conn) == []


def test_production_mode_without_hint_when_organizer_exists(monkeypatch, capsys, conn):
    auth.create_organizer(conn, "coach", "real-password")
    monkeypatch.setenv("TEAMPLAN_MODE", "production")
    assert startup.main() == 0
    assert "create_organizer.py" not in capsys.readouterr().out
    assert users(conn) == ["coach"]


@pytest.mark.parametrize("argv", [[], ["--reset"]])
def test_seed_refuses_in_production(monkeypatch, conn, argv):
    auth.create_organizer(conn, "coach", "real-password")
    monkeypatch.setenv("TEAMPLAN_MODE", "production")
    assert seed.main(argv) == 1
    assert users(conn) == ["coach"]
