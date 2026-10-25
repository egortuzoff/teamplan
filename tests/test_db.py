"""Схема базы PostgreSQL: ограничения, внешние ключи, логин без учёта регистра (ТР-2, ТР-7)."""
import threading

import psycopg
import pytest
from psycopg import errors, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from teamplan import auth, events, players
from teamplan.db import NO_DATABASE_URL, TABLES, clear, connect, transaction
from teamplan.errors import ServiceError

TS = "2026-10-01 10:00:00"


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


def _insert_event(conn, event_type="training"):
    return conn.execute(
        "INSERT INTO events (type, title, date, time, created_at, updated_at) "
        "VALUES (%s, 'Т', '2026-11-10', '10:00', %s, %s) RETURNING id",
        (event_type, TS, TS),
    ).fetchone()["id"]


def _insert_player(conn, name="Игрок"):
    return conn.execute(
        "INSERT INTO players (name, created_at, updated_at) VALUES (%s, %s, %s) RETURNING id",
        (name, TS, TS),
    ).fetchone()["id"]


# ---------- создание схемы ----------

def test_schema_has_all_tables(conn):
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
    ).fetchall()
    assert set(TABLES) <= {r["table_name"] for r in rows}


def test_repeated_connect_keeps_data(conn, database_url):
    _insert_player(conn)
    connect(database_url).close()
    assert _count(conn, "players") == 1


def test_simultaneous_first_connects_create_schema_once(conn, database_url):
    """Несколько страниц открылись одновременно на пустой базе — схема создаётся без ошибок."""
    conn.execute(sql.SQL("DROP TABLE {} CASCADE").format(
        sql.SQL(", ").join(sql.Identifier(t) for t in TABLES)
    ))
    barrier = threading.Barrier(4)
    failures = []

    def page():
        try:
            barrier.wait()
            connect(database_url).close()
        except Exception as exc:
            failures.append(exc)

    threads = [threading.Thread(target=page) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert failures == []
    assert _count(conn, "notifications") == 0


def test_clear_removes_data_and_restarts_ids(conn):
    _insert_player(conn)
    clear(conn)
    assert _count(conn, "players") == 0
    assert _insert_player(conn) == 1


# ---------- ограничения CHECK ----------

@pytest.mark.parametrize("sql_text, params", [
    ("INSERT INTO events (type, title, date, time, created_at, updated_at) "
     "VALUES ('camp', 'Т', '2026-11-10', '10:00', %s, %s)", (TS, TS)),
    ("INSERT INTO events (type, title, date, time, status, created_at, updated_at) "
     "VALUES ('game', 'Т', '2026-11-10', '10:00', 'done', %s, %s)", (TS, TS)),
    ("INSERT INTO users (login, password_hash, role, created_at, updated_at) "
     "VALUES ('x', 'h', 'admin', %s, %s)", (TS, TS)),
    # Игрок без связанной карточки игрока.
    ("INSERT INTO users (login, password_hash, role, created_at, updated_at) "
     "VALUES ('x', 'h', 'player', %s, %s)", (TS, TS)),
])
def test_check_constraints(conn, sql_text, params):
    with pytest.raises(errors.CheckViolation):
        conn.execute(sql_text, params)


def test_check_constraints_on_results_goals_and_notifications(conn):
    event_id = _insert_event(conn, "game")
    player_id = _insert_player(conn)
    with pytest.raises(errors.CheckViolation):
        conn.execute(
            "INSERT INTO results (event_id, our_score, their_score, created_at, updated_at) "
            "VALUES (%s, -1, 0, %s, %s)", (event_id, TS, TS),
        )
    conn.execute(
        "INSERT INTO results (event_id, our_score, their_score, created_at, updated_at) "
        "VALUES (%s, 1, 0, %s, %s)", (event_id, TS, TS),
    )
    with pytest.raises(errors.CheckViolation):  # гол и передача одного игрока
        conn.execute(
            "INSERT INTO goals (event_id, scorer_id, assist_id) VALUES (%s, %s, %s)",
            (event_id, player_id, player_id),
        )
    with pytest.raises(errors.CheckViolation):
        conn.execute(
            "INSERT INTO attendance (event_id, player_id, status, updated_at) "
            "VALUES (%s, %s, 'late', %s)", (event_id, player_id, TS),
        )
    user_id = auth.create_organizer(conn, "trener", "pass-org")
    with pytest.raises(errors.CheckViolation):
        conn.execute(
            "INSERT INTO notifications (user_id, event_id, kind, text, created_at) "
            "VALUES (%s, %s, 'unknown', 'т', %s)", (user_id, event_id, TS),
        )


# ---------- внешние ключи ----------

def test_foreign_keys_reject_missing_rows(conn):
    with pytest.raises(errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO attendance (event_id, player_id, status, updated_at) "
            "VALUES (999, 999, 'yes', %s)", (TS,),
        )


def test_deleting_event_cascades(conn):
    event_id = _insert_event(conn, "game")
    player_id = _insert_player(conn)
    conn.execute(
        "INSERT INTO attendance (event_id, player_id, status, updated_at) "
        "VALUES (%s, %s, 'yes', %s)", (event_id, player_id, TS),
    )
    conn.execute(
        "INSERT INTO squads (event_id, state, created_at, updated_at) "
        "VALUES (%s, 'published', %s, %s)", (event_id, TS, TS),
    )
    conn.execute(
        "INSERT INTO squad_players (event_id, player_id) VALUES (%s, %s)", (event_id, player_id)
    )
    conn.execute("DELETE FROM events WHERE id = %s", (event_id,))
    for table in ("attendance", "squads", "squad_players"):
        assert _count(conn, table) == 0


def test_event_with_result_and_scorer_cannot_be_deleted(conn):
    event_id = _insert_event(conn, "game")
    player_id = _insert_player(conn)
    conn.execute(
        "INSERT INTO results (event_id, our_score, their_score, created_at, updated_at) "
        "VALUES (%s, 1, 0, %s, %s)", (event_id, TS, TS),
    )
    conn.execute(
        "INSERT INTO goals (event_id, scorer_id) VALUES (%s, %s)", (event_id, player_id)
    )
    with pytest.raises(errors.ForeignKeyViolation):
        conn.execute("DELETE FROM events WHERE id = %s", (event_id,))
    with pytest.raises(errors.ForeignKeyViolation):
        conn.execute("DELETE FROM players WHERE id = %s", (player_id,))


def test_deleting_user_removes_notifications_and_player_keeps_user_link_null(conn):
    org_id = auth.create_organizer(conn, "trener", "pass-org", player_name="Тренер Играющий")
    event_id = _insert_event(conn)
    conn.execute(
        "INSERT INTO notifications (user_id, event_id, kind, text, created_at) "
        "VALUES (%s, %s, 'moved', 'т', %s)", (org_id, event_id, TS),
    )
    player_id = conn.execute(
        "SELECT player_id FROM users WHERE id = %s", (org_id,)
    ).fetchone()["player_id"]
    conn.execute("DELETE FROM players WHERE id = %s", (player_id,))
    row = conn.execute("SELECT player_id FROM users WHERE id = %s", (org_id,)).fetchone()
    assert row["player_id"] is None  # ON DELETE SET NULL
    conn.execute("DELETE FROM users WHERE id = %s", (org_id,))
    assert _count(conn, "notifications") == 0


# ---------- логин без учёта регистра (FR-2, FR-4) ----------

def test_login_is_unique_ignoring_case_in_database(conn):
    auth.create_organizer(conn, "Trener", "pass-org")
    with pytest.raises(errors.UniqueViolation):
        conn.execute(
            "INSERT INTO users (login, password_hash, role, created_at, updated_at) "
            "VALUES ('TRENER', 'h', 'organizer', %s, %s)", (TS, TS),
        )


def test_login_ignores_case_in_service(conn):
    auth.create_organizer(conn, "Trener", "pass-org")
    assert auth.login(conn, "trener", "pass-org").login == "Trener"
    assert auth.login(conn, "TRENER", "pass-org").login == "Trener"
    org = auth.login(conn, "Trener", "pass-org")
    with pytest.raises(ServiceError, match="Такой логин уже занят"):
        players.create_player_account(conn, org, "Игрок", "trener", "pass-1")
    with pytest.raises(ServiceError, match="Такой логин уже занят"):
        auth.create_organizer(conn, "tReNeR", "pass-2")


# ---------- транзакции (ТС-3) ----------

def test_transaction_turns_database_errors_into_message(conn):
    with pytest.raises(ServiceError, match="Не удалось сохранить данные"):
        with transaction(conn):
            _insert_player(conn)
            _insert_event(conn, "camp")  # нарушение CHECK
    assert _count(conn, "players") == 0


def test_serialized_transactions_run_one_after_another(database_url):
    """serialized=True: вторая транзакция ждёт, пока завершится первая."""
    first, second = connect(database_url), connect(database_url)
    order = []
    try:
        def run_second():
            with transaction(second, serialized=True):
                order.append("second")

        with transaction(first, serialized=True):
            worker = threading.Thread(target=run_second)
            worker.start()
            worker.join(timeout=1)
            assert worker.is_alive()
            order.append("first")
        worker.join(timeout=10)
        assert order == ["first", "second"]
    finally:
        first.close()
        second.close()


# ---------- адрес базы (ТР-7) ----------

def test_missing_database_url_gives_clear_message(monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(RuntimeError) as exc:
        connect()
    assert str(exc.value) == NO_DATABASE_URL


def test_database_url_switches_database(conn, database_url, monkeypatch):
    """Смена DATABASE_URL переключает базу без правки кода (AC-21)."""
    other_name = conninfo_to_dict(database_url)["dbname"] + "_other"
    other_url = make_conninfo(database_url, dbname=other_name)
    with psycopg.connect(make_conninfo(database_url, dbname="postgres"), autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
            sql.Identifier(other_name)))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(other_name)))
    try:
        auth.create_organizer(conn, "trener", "pass-org")
        monkeypatch.setenv("DATABASE_URL", other_url)
        other = connect()
        try:
            assert _count(other, "users") == 0  # новая пустая база со схемой
            auth.create_organizer(other, "boss", "boss-pass")
            org = auth.login(other, "boss", "boss-pass")
            events.create_event(other, org, "training", "Т", "2030-01-01", "10:00")
        finally:
            other.close()
        assert [r["login"] for r in conn.execute("SELECT login FROM users")] == ["trener"]
        assert _count(conn, "events") == 0
    finally:
        with psycopg.connect(make_conninfo(database_url, dbname="postgres"),
                             autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(other_name)))
