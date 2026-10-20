from datetime import datetime

import pytest

from teamplan import attendance
from teamplan.errors import ServiceError


def test_player_marks_own_attendance(conn, p1, mk_event, now):
    event_id = mk_event()
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "yes", now=now)
    summary = attendance.attendance_summary(conn, p1, event_id, now=now)
    assert summary["counts"]["yes"] == 1
    assert summary["marks"][p1.player_id] == "yes"


def test_player_cannot_mark_for_another_player(conn, p1, p2, mk_event, now):
    event_id = mk_event()
    with pytest.raises(ServiceError, match="только за себя"):
        attendance.set_attendance(conn, p1, event_id, p2.player_id, "yes", now=now)


def test_organizer_can_mark_for_any_player(conn, org, p2, mk_event, now):
    event_id = mk_event()
    attendance.set_attendance(conn, org, event_id, p2.player_id, "maybe", now=now)
    summary = attendance.attendance_summary(conn, org, event_id, now=now)
    assert summary["marks"][p2.player_id] == "maybe"


def test_new_mark_replaces_the_old_one(conn, p1, mk_event, now):
    event_id = mk_event()
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "yes", now=now)
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "no", now=now)
    summary = attendance.attendance_summary(conn, p1, event_id, now=now)
    assert summary["counts"]["yes"] == 0 and summary["counts"]["no"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM attendance").fetchone()["n"] == 1


def test_mark_cannot_be_changed_after_start(conn, p1, mk_event, now):
    event_id = mk_event()  # начало 2026-11-05 19:00
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "yes", now=now)
    with pytest.raises(ServiceError, match="уже началось"):
        attendance.set_attendance(
            conn, p1, event_id, p1.player_id, "no", now=datetime(2026, 11, 5, 19, 0)
        )
    summary = attendance.attendance_summary(conn, p1, event_id, now=now)
    assert summary["marks"][p1.player_id] == "yes"


def test_no_attendance_on_games(conn, p1, mk_event, now):
    game = mk_event("game")
    with pytest.raises(ServiceError, match="На игры явка не отмечается"):
        attendance.set_attendance(conn, p1, game, p1.player_id, "yes", now=now)
    with pytest.raises(ServiceError, match="Для игры сводка явки не строится"):
        attendance.attendance_summary(conn, p1, game, now=now)


def test_invalid_status_is_rejected(conn, p1, mk_event, now):
    event_id = mk_event()
    with pytest.raises(ServiceError, match="Выберите вариант"):
        attendance.set_attendance(conn, p1, event_id, p1.player_id, "когда-нибудь", now=now)


def test_cancelled_event_does_not_accept_marks(conn, org, p1, mk_event, now):
    from teamplan import events

    event_id = mk_event()
    events.cancel_event(conn, org, event_id, now=now)
    with pytest.raises(ServiceError, match="отменено"):
        attendance.set_attendance(conn, p1, event_id, p1.player_id, "yes", now=now)


def test_summary_counts_answered_and_missing(conn, org, p1, p2, p3, mk_event, now):
    event_id = mk_event()
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "yes", now=now)
    attendance.set_attendance(conn, p2, event_id, p2.player_id, "maybe", now=now)
    counts = attendance.attendance_summary(conn, org, event_id, now=now)["counts"]
    assert counts == {"yes": 1, "maybe": 1, "no": 0, "none": 1}


def test_missing_players_highlighted_only_after_reply_by(conn, org, p1, mk_event, now):
    event_id = mk_event(reply_by="2026-11-03 18:00")
    before = attendance.attendance_summary(conn, org, event_id, now=now)
    after = attendance.attendance_summary(conn, org, event_id, now=datetime(2026, 11, 3, 18, 1))
    assert before["highlight_missing"] is False
    assert after["highlight_missing"] is True and after["overdue"] is True


def test_no_highlight_without_reply_by(conn, org, p1, mk_event):
    event_id = mk_event()
    summary = attendance.attendance_summary(conn, org, event_id, now=datetime(2026, 12, 1))
    assert summary["highlight_missing"] is False
