"""Обновление базы старого формата (ТЗ 0.1: тип camp, нет времени сбора)."""
import sqlite3

import pytest

from teamplan import attendance, auth, events, squads
from teamplan.db import connect

# Таблицы в том виде, в каком их создавала версия приложения по ТЗ 0.1.
OLD_SCHEMA = """
CREATE TABLE players (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, number INTEGER,
    position TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    login TEXT NOT NULL UNIQUE COLLATE NOCASE, password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('organizer', 'player')),
    player_id INTEGER UNIQUE REFERENCES players(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    CHECK (role = 'organizer' OR player_id IS NOT NULL)
);
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK (type IN ('training', 'camp', 'game')),
    title TEXT NOT NULL, date TEXT NOT NULL, time TEXT NOT NULL, place TEXT,
    status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'cancelled')),
    reply_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE attendance (
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('yes', 'no', 'maybe')),
    updated_at TEXT NOT NULL, PRIMARY KEY (event_id, player_id)
);
CREATE TABLE squads (
    event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK (state IN ('draft', 'published')),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE squad_players (
    event_id INTEGER NOT NULL REFERENCES squads(event_id) ON DELETE CASCADE,
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE RESTRICT,
    PRIMARY KEY (event_id, player_id)
);
"""

TS = "2026-10-01 10:00:00"


@pytest.fixture
def old_db(tmp_path):
    """Файл базы старого формата: игрок, сбор с отметкой и игра с составом."""
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript(OLD_SCHEMA)
    old.execute("INSERT INTO players VALUES (1, 'Игрок Первый', 10, NULL, ?, ?)", (TS, TS))
    old.execute(
        "INSERT INTO users VALUES (1, 'igrok1', ?, 'player', 1, ?, ?)",
        (auth.hash_password("pass-igrok1"), TS, TS),
    )
    old.execute(
        "INSERT INTO events VALUES (1, 'camp', 'Выездной сбор', '2026-11-10', '10:00', "
        "'База', 'planned', '2026-11-09 18:00', ?, ?)", (TS, TS),
    )
    old.execute(
        "INSERT INTO events VALUES (2, 'game', 'Игра', '2026-11-12', '19:30', "
        "'Стадион', 'planned', NULL, ?, ?)", (TS, TS),
    )
    old.execute("INSERT INTO attendance VALUES (1, 1, 'yes', ?)", (TS,))
    old.execute("INSERT INTO squads VALUES (2, 'published', ?, ?)", (TS, TS))
    old.execute("INSERT INTO squad_players VALUES (2, 1)")
    old.commit()
    old.close()
    return path


def test_old_database_is_upgraded_without_data_loss(old_db, now):
    conn = connect(old_db)
    try:
        player = auth.login(conn, "igrok1", "pass-igrok1")
        camp = events.get_event(conn, player, 1)
        assert camp["type"] == "activity"
        assert camp["type_label"] == "Мероприятие"
        assert camp["title"] == "Выездной сбор"
        assert camp["reply_by"] == "2026-11-09 18:00"
        assert camp["gather_time"] is None
        assert events.get_event(conn, player, 2)["type"] == "game"

        summary = attendance.attendance_summary(conn, player, 1, now=now)
        assert summary["marks"] == {1: "yes"}
        squad = squads.get_squad(conn, player, 2)
        assert squad["state"] == "published"
        assert [p["id"] for p in squad["players"]] == [1]
    finally:
        conn.close()


def test_upgraded_database_keeps_constraints(old_db):
    connect(old_db).close()
    conn = connect(old_db)  # повторный запуск ничего не меняет
    try:
        assert conn.execute("SELECT type FROM events WHERE id = 1").fetchone()[0] == "activity"
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO events (type, title, date, time, created_at, updated_at) "
                "VALUES ('camp', 'Т', '2026-11-10', '10:00', 't', 't')"
            )
        # Внешние ключи работают: удаление события удаляет отметки и состав.
        conn.execute("DELETE FROM events")
        for table in ("attendance", "squads", "squad_players"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    finally:
        conn.close()


# ---------- обновление базы этапа 1 (ТЗ 0.2) до этапа 2 ----------

# Таблицы в том виде, в каком их создавала версия этапа 1 (ТЗ 0.2):
# без результатов, голов и уведомлений.
STAGE1_SCHEMA = OLD_SCHEMA.replace(
    "type TEXT NOT NULL CHECK (type IN ('training', 'camp', 'game')),\n"
    "    title TEXT NOT NULL, date TEXT NOT NULL, time TEXT NOT NULL, place TEXT,",
    "type TEXT NOT NULL CHECK (type IN ('training', 'game', 'activity')),\n"
    "    title TEXT NOT NULL, date TEXT NOT NULL, time TEXT NOT NULL, gather_time TEXT,\n"
    "    place TEXT,",
)


@pytest.fixture
def stage1_db(tmp_path):
    """База этапа 1: игрок, организатор, тренировка с отметкой и прошедшая игра с составом."""
    path = str(tmp_path / "stage1.db")
    old = sqlite3.connect(path)
    old.executescript(STAGE1_SCHEMA)
    old.execute("INSERT INTO players VALUES (1, 'Игрок Первый', 10, NULL, ?, ?)", (TS, TS))
    old.execute(
        "INSERT INTO users VALUES (1, 'igrok1', ?, 'player', 1, ?, ?)",
        (auth.hash_password("pass-igrok1"), TS, TS),
    )
    old.execute(
        "INSERT INTO users VALUES (2, 'trener', ?, 'organizer', NULL, ?, ?)",
        (auth.hash_password("pass-org"), TS, TS),
    )
    old.execute(
        "INSERT INTO events (id, type, title, date, time, gather_time, place, status, "
        "reply_by, created_at, updated_at) VALUES (1, 'training', 'Тренировка', "
        "'2026-11-10', '19:00', '18:30', 'Зал', 'planned', NULL, ?, ?)", (TS, TS),
    )
    old.execute(
        "INSERT INTO events (id, type, title, date, time, gather_time, place, status, "
        "reply_by, created_at, updated_at) VALUES (2, 'game', 'Игра', '2026-10-31', "
        "'19:30', NULL, 'Стадион', 'planned', NULL, ?, ?)", (TS, TS),
    )
    old.execute("INSERT INTO attendance VALUES (1, 1, 'yes', ?)", (TS,))
    old.execute("INSERT INTO squads VALUES (2, 'published', ?, ?)", (TS, TS))
    old.execute("INSERT INTO squad_players VALUES (2, 1)")
    old.commit()
    old.close()
    return path


def test_stage1_database_gets_new_tables_without_data_loss(stage1_db, now):
    from teamplan import notifications, results

    conn = connect(stage1_db)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"results", "goals", "notifications"} <= tables

        org = auth.login(conn, "trener", "pass-org")
        player = auth.login(conn, "igrok1", "pass-igrok1")
        training = events.get_event(conn, player, 1)
        assert (training["title"], training["gather_time"]) == ("Тренировка", "18:30")
        assert attendance.attendance_summary(conn, player, 1, now=now)["marks"] == {1: "yes"}
        assert [p["id"] for p in squads.get_squad(conn, player, 2)["players"]] == [1]

        # Новые функции работают на обновлённой базе.
        results.save_result(conn, org, 2, 1, 0, [(1, None)], now=now)
        assert results.player_stats(conn, player)[0]["goals"] == 1
        assert notifications.unread_count(conn, player) == 1
    finally:
        conn.close()

    conn = connect(stage1_db)  # повторный запуск сохраняет данные этапа 2
    try:
        assert conn.execute("SELECT our_score FROM results WHERE event_id = 2").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 1
    finally:
        conn.close()
