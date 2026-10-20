"""Тестовые данные seed.py (п. 7.3) и сверка статистики с ручным подсчётом (AC-7)."""
import sys

import pytest

import seed
from teamplan import auth, events, home, notifications, results
from teamplan.db import connect


@pytest.fixture
def db_url(database_url, monkeypatch):
    """Пустая база тестов (conftest.py): seed.py подключается к ней по DATABASE_URL."""
    monkeypatch.setattr(sys, "argv", ["seed.py"])
    return database_url


def _played_game(conn, actor):
    return next(e for e in events.list_events(conn, actor) if e["title"] == seed.PLAYED_GAME_TITLE)


def test_seed_creates_played_game_and_stats_match_manual_count(db_url):
    assert seed.main() == 0
    conn = connect(db_url)
    try:
        player = auth.login(conn, "igrok3", "igrok3-demo")
        game = _played_game(conn, player)
        assert events.is_started(game)
        result = results.get_result(conn, player, game["id"])
        assert (result["our_score"], result["their_score"]) == (2, 1)
        # У обеих игр вымышленные соперники (ТБ-5).
        assert game["opponent"] == seed.PLAYED_GAME_OPPONENT
        future = [e for e in events.list_events(conn, player)
                  if e["type"] == "game" and not events.is_started(e)]
        assert [e["opponent"] for e in future] == [
            seed.FUTURE_GAME_OPPONENT, seed.DRAFT_GAME_OPPONENT,
        ]
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
        assert types == ["activity", "game", "game", "game", "game", "training"]
        first = auth.login(conn, "igrok1", "igrok1-demo")
        assert notifications.unread_count(conn, first) > 0
        assert notifications.unread_count(conn, player) == 0  # не в составах
    finally:
        conn.close()


def test_seed_data_for_home_screens(db_url):
    """Тестовые данные для проверки главного экрана (AC-19)."""
    seed.main()
    conn = connect(db_url)
    try:
        first = auth.login(conn, "igrok1", "igrok1-demo")
        data = home.player_home(conn, first)
        assert data["next_event"] is not None
        last = data["last_match"]
        assert (last["our_score"], last["their_score"], last["outcome"]) == (2, 1, "Победа")
        assert data["stats"] == {"games": 1, "goals": 2, "assists": 0}
        week_types = [e["type"] for e in data["week"]]
        assert week_types == ["training", "game"]
        assert data["week"][0]["my_mark"] == "yes"
        assert data["week"][1]["in_squad"] is True

        org = auth.login(conn, "trener", "trener-demo-1")
        data = home.organizer_home(conn, org)
        assert data["needs_action"]["title"] == seed.DRAFT_GAME_TITLE
        assert data["needs_action"]["squad_state"] == "draft"
        assert [g["title"] for g in data["no_score"]] == [seed.NO_SCORE_GAME_TITLE]
        assert [e["type"] for e in data["week_attendance"]] == ["training"]
        assert data["week_attendance"][0]["counts"] == {"yes": 1, "maybe": 0, "no": 0, "none": 2}
    finally:
        conn.close()


def test_seed_tops_up_previous_version_with_home_games(db_url, monkeypatch):
    with monkeypatch.context() as m:  # база версии 0.6: без игр для главного экрана
        m.setattr(seed, "add_draft_game", lambda *args: None)
        m.setattr(seed, "add_no_score_game", lambda *args: None)
        seed.main()
    conn = connect(db_url)
    org = auth.login(conn, "trener", "trener-demo-1")
    before = len(events.list_events(conn, org))
    conn.close()

    seed.main()
    seed.main()  # повторный запуск ничего не добавляет
    conn = connect(db_url)
    try:
        titles = [e["title"] for e in events.list_events(conn, org)]
        assert len(titles) == before + 2
        assert seed.DRAFT_GAME_TITLE in titles and seed.NO_SCORE_GAME_TITLE in titles
    finally:
        conn.close()


def test_seed_tops_up_stage1_test_data_once(db_url, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(seed, "add_played_game", lambda *args: None)  # база этапа 1
        seed.main()
    conn = connect(db_url)
    org = auth.login(conn, "trener", "trener-demo-1")
    before = len(events.list_events(conn, org))
    conn.close()

    seed.main()
    seed.main()  # повторный запуск ничего не добавляет
    conn = connect(db_url)
    try:
        evs = events.list_events(conn, org)
        assert len(evs) == before + 1
        assert results.get_result(conn, org, _played_game(conn, org)["id"])["our_score"] == 2
    finally:
        conn.close()


def test_seed_does_not_touch_foreign_data(db_url):
    conn = connect(db_url)
    auth.create_organizer(conn, "real-boss", "real-pass")
    conn.close()
    seed.main()
    conn = connect(db_url)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 1
    finally:
        conn.close()
