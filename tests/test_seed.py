"""Тестовые данные seed.py (п. 7.3) и сверка статистики с ручным подсчётом (AC-7)."""
import sys

import pytest

import seed
from teamplan import auth, events, notifications, results
from teamplan.db import connect


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "seed.db")
    monkeypatch.setenv("TEAMPLAN_DB", path)
    monkeypatch.setattr(sys, "argv", ["seed.py"])
    return path


def _played_game(conn, actor):
    return next(e for e in events.list_events(conn, actor) if e["title"] == seed.PLAYED_GAME_TITLE)


def test_seed_creates_played_game_and_stats_match_manual_count(db_path):
    assert seed.main() == 0
    conn = connect(db_path)
    try:
        player = auth.login(conn, "igrok3", "igrok3-demo")
        game = _played_game(conn, player)
        assert events.is_started(game)
        result = results.get_result(conn, player, game["id"])
        assert (result["our_score"], result["their_score"]) == (2, 1)
        assert result["warning"] is None
        # Ручной подсчёт: оба гола у Игрока Первого, одна передача у Игрока Второго.
        # Будущая игра без счёта в «игры» не входит.
        table = {r["name"]: (r["games"], r["goals"], r["assists"])
                 for r in results.player_stats(conn, player)}
        assert table == {
            "Игрок Первый": (1, 2, 0),
            "Игрок Второй": (1, 0, 1),
            "Игрок Третий": (0, 0, 0),
        }
        types = sorted(e["type"] for e in events.list_events(conn, player))
        assert types == ["activity", "game", "game", "training"]
        first = auth.login(conn, "igrok1", "igrok1-demo")
        assert notifications.unread_count(conn, first) > 0
        assert notifications.unread_count(conn, player) == 0  # не в составах
    finally:
        conn.close()


def test_seed_tops_up_stage1_test_data_once(db_path, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(seed, "add_played_game", lambda *args: None)  # база этапа 1
        seed.main()
    conn = connect(db_path)
    org = auth.login(conn, "trener", "trener-demo-1")
    before = len(events.list_events(conn, org))
    conn.close()

    seed.main()
    seed.main()  # повторный запуск ничего не добавляет
    conn = connect(db_path)
    try:
        evs = events.list_events(conn, org)
        assert len(evs) == before + 1
        assert results.get_result(conn, org, _played_game(conn, org)["id"])["our_score"] == 2
    finally:
        conn.close()


def test_seed_does_not_touch_foreign_data(db_path):
    conn = connect(db_path)
    auth.create_organizer(conn, "real-boss", "real-pass")
    conn.close()
    seed.main()
    conn = connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    finally:
        conn.close()
