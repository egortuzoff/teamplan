import sqlite3
from datetime import datetime, time, timedelta

import pytest

from teamplan import attendance, events, squads
from teamplan.db import transaction
from teamplan.errors import ServiceError


def test_past_date_is_rejected(conn, org, now):
    yesterday = now.date() - timedelta(days=1)
    with pytest.raises(ServiceError, match="прошлом"):
        events.create_event(conn, org, "training", "Тренировка", yesterday, time(19, 0), now=now)


def test_today_is_allowed(conn, org, now):
    events.create_event(conn, org, "training", "Тренировка", now.date(), time(19, 0), now=now)


def test_empty_title_is_rejected(conn, org, now):
    with pytest.raises(ServiceError, match="Название"):
        events.create_event(conn, org, "training", "   ", now.date(), time(19, 0), now=now)


def test_unknown_type_is_rejected(conn, org, now):
    with pytest.raises(ServiceError, match="Тип события"):
        events.create_event(conn, org, "concert", "Что-то", now.date(), time(19, 0), now=now)


def test_player_cannot_create_edit_cancel_or_delete(conn, p1, mk_event, now):
    event_id = mk_event()
    with pytest.raises(ServiceError, match="только организатору"):
        events.create_event(conn, p1, "training", "Т", now.date(), time(19, 0), now=now)
    with pytest.raises(ServiceError, match="только организатору"):
        events.update_event(conn, p1, event_id, "Т", now.date(), time(19, 0), now=now)
    with pytest.raises(ServiceError, match="только организатору"):
        events.cancel_event(conn, p1, event_id, now=now)
    with pytest.raises(ServiceError, match="только организатору"):
        events.delete_event(conn, p1, event_id)


def test_reply_by_is_not_allowed_for_game(conn, mk_event):
    with pytest.raises(ServiceError, match="Для игры срок ответа не задаётся"):
        mk_event("game", reply_by="2026-11-04 18:00")


def test_events_are_sorted_by_date_and_time(conn, org, mk_event):
    late = mk_event(days=5, title="Позже")
    early = mk_event(days=2, title="Раньше")
    assert [e["id"] for e in events.list_events(conn, org)] == [early, late]


def test_cancel_keeps_event_in_list(conn, org, mk_event, now):
    event_id = mk_event()
    events.cancel_event(conn, org, event_id, now=now)
    event = events.get_event(conn, org, event_id)
    assert event["status"] == "cancelled"
    assert [e["id"] for e in events.list_events(conn, org)] == [event_id]


def test_started_event_cannot_be_cancelled(conn, org, mk_event):
    event_id = mk_event()  # начало 2026-11-05 19:00
    with pytest.raises(ServiceError, match="уже началось"):
        events.cancel_event(conn, org, event_id, now=datetime(2026, 11, 5, 19, 1))


def test_cancelled_event_cannot_be_cancelled_or_edited_again(conn, org, mk_event, now):
    event_id = mk_event()
    events.cancel_event(conn, org, event_id, now=now)
    with pytest.raises(ServiceError, match="уже отменено"):
        events.cancel_event(conn, org, event_id, now=now)
    with pytest.raises(ServiceError, match="Отменённое событие"):
        events.update_event(conn, org, event_id, "Т", now.date(), time(19, 0), now=now)


def test_update_keeps_attendance_marks(conn, org, p1, mk_event, now):
    event_id = mk_event()
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "yes", now=now)
    events.update_event(
        conn, org, event_id, "Новое название", now.date() + timedelta(days=4), time(20, 0), "Зал", now=now
    )
    summary = attendance.attendance_summary(conn, org, event_id, now=now)
    assert summary["counts"]["yes"] == 1
    assert events.get_event(conn, org, event_id)["time"] == "20:00"


def test_update_rejects_moving_to_the_past(conn, org, mk_event, now):
    event_id = mk_event()
    with pytest.raises(ServiceError, match="прошлом"):
        events.update_event(conn, org, event_id, "Т", now.date() - timedelta(days=1), time(19, 0), now=now)


def test_delete_event_removes_marks_and_squad(conn, org, p1, mk_event, now):
    training = mk_event()
    game = mk_event("game")
    attendance.set_attendance(conn, p1, training, p1.player_id, "yes", now=now)
    squads.save_squad(conn, org, game, [p1.player_id])
    squads.publish_squad(conn, org, game)

    events.delete_event(conn, org, training)
    events.delete_event(conn, org, game)

    for table in ("events", "attendance", "squads", "squad_players"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_transaction_rolls_back_on_database_error(conn):
    with pytest.raises(ServiceError, match="Не удалось сохранить данные"):
        with transaction(conn):
            conn.execute(
                "INSERT INTO players (name, created_at, updated_at) VALUES ('X', 't', 't')"
            )
            raise sqlite3.OperationalError("disk I/O error")
    assert conn.execute("SELECT COUNT(*) FROM players").fetchone()[0] == 0
