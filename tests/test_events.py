import psycopg
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


# FR-10: пустое название, название из пробелов и из невидимых символов
# (нулевой ширины пробел, BOM, заполнитель хангыль и т. п.) отклоняются.
BLANK_TITLES = [
    "", None, " ", "     ", "\t\n", "\u00a0\u3000",
    "\u200b", "\u200b \u200b", "\ufeff", "\u2060", "\u200e", "\u00ad", "\u3164", "\u2800",
]


@pytest.mark.parametrize("title", BLANK_TITLES)
@pytest.mark.parametrize("event_type", ["training", "game", "activity"])
def test_blank_title_is_rejected_on_create(conn, org, now, title, event_type):
    with pytest.raises(ServiceError, match="Название: поле обязательно для заполнения"):
        events.create_event(conn, org, event_type, title, now.date(), time(19, 0), now=now)
    assert events.list_events(conn, org) == []


@pytest.mark.parametrize("title", BLANK_TITLES)
def test_blank_title_is_rejected_on_update(conn, org, mk_event, now, title):
    ev = mk_event(title="Тренировка")
    old = events.get_event(conn, org, ev)
    with pytest.raises(ServiceError, match="Название: поле обязательно для заполнения"):
        events.update_event(conn, org, ev, title, old["date"], old["time"], now=now)
    assert events.get_event(conn, org, ev)["title"] == "Тренировка"


def test_title_is_trimmed_and_text_with_spaces_is_kept(conn, org, now):
    ev = events.create_event(
        conn, org, "training", "  Лёд  2  ", now.date(), time(19, 0), now=now
    )
    assert events.get_event(conn, org, ev)["title"] == "Лёд  2"


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
        assert conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 0


def test_transaction_rolls_back_on_database_error(conn):
    with pytest.raises(ServiceError, match="Не удалось сохранить данные"):
        with transaction(conn):
            conn.execute(
                "INSERT INTO players (name, created_at, updated_at) VALUES ('X', 't', 't')"
            )
            raise psycopg.OperationalError("server closed the connection unexpectedly")
    assert conn.execute("SELECT COUNT(*) AS n FROM players").fetchone()["n"] == 0


def test_activity_has_attendance_and_reply_by(conn, org, p1, mk_event, now):
    event_id = mk_event("activity", reply_by="2026-11-04 18:00")
    event = events.get_event(conn, org, event_id)
    assert event["type_label"] == "Мероприятие"
    assert event["reply_by"] == "2026-11-04 18:00"
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "maybe", now=now)
    summary = attendance.attendance_summary(conn, org, event_id, now=now)
    assert summary["counts"]["maybe"] == 1


@pytest.mark.parametrize("event_type", ["training", "game", "activity"])
def test_gather_time_is_saved_for_any_event_type(conn, org, mk_event, event_type):
    event_id = mk_event(event_type, gather_time=time(18, 30))
    assert events.get_event(conn, org, event_id)["gather_time"] == "18:30"
    assert events.list_events(conn, org)[0]["gather_time"] == "18:30"


def test_gather_time_is_optional(conn, org, mk_event):
    assert events.get_event(conn, org, mk_event())["gather_time"] is None


def test_gather_time_equal_to_start_is_allowed(conn, org, mk_event):
    event_id = mk_event(gather_time=time(19, 0))
    assert events.get_event(conn, org, event_id)["gather_time"] == "19:00"


def test_gather_time_after_start_is_rejected(conn, mk_event):
    with pytest.raises(ServiceError, match="не может быть позже времени начала"):
        mk_event("game", gather_time=time(19, 15))


def test_gather_time_wrong_format_is_rejected(conn, mk_event):
    with pytest.raises(ServiceError, match="Время сбора"):
        mk_event(gather_time="полседьмого")


def test_update_gather_time_keeps_attendance_marks(conn, org, p1, mk_event, now):
    event_id = mk_event(gather_time=time(18, 30))
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "yes", now=now)
    day = now.date() + timedelta(days=3)
    events.update_event(conn, org, event_id, "Т", day, time(19, 0), gather_time=time(18, 0), now=now)
    assert events.get_event(conn, org, event_id)["gather_time"] == "18:00"
    assert attendance.attendance_summary(conn, org, event_id, now=now)["counts"]["yes"] == 1
    events.update_event(conn, org, event_id, "Т", day, time(19, 0), now=now)
    assert events.get_event(conn, org, event_id)["gather_time"] is None


def test_update_rejects_start_earlier_than_gather_time(conn, org, mk_event, now):
    event_id = mk_event(gather_time=time(18, 30))
    day = now.date() + timedelta(days=3)
    with pytest.raises(ServiceError, match="не может быть позже времени начала"):
        events.update_event(conn, org, event_id, "Т", day, time(18, 0), gather_time=time(18, 30), now=now)


def test_update_sets_gather_time_for_activity(conn, org, mk_event, now):
    event_id = mk_event("activity")
    day = now.date() + timedelta(days=3)
    events.update_event(conn, org, event_id, "Т", day, time(19, 0), gather_time=time(18, 0), now=now)
    assert events.get_event(conn, org, event_id)["gather_time"] == "18:00"


# ---------- срок «ответить до» (FR-42) ----------

def test_reply_by_in_the_past_is_rejected(conn, mk_event):
    with pytest.raises(ServiceError, match="не может быть в прошлом"):
        mk_event(reply_by="2026-11-02 11:59")


def test_reply_by_now_is_allowed(conn, org, mk_event):
    event_id = mk_event(reply_by="2026-11-02 12:00")
    assert events.get_event(conn, org, event_id)["reply_by"] == "2026-11-02 12:00"


def test_reply_by_after_start_is_rejected(conn, mk_event):
    # Событие 2026-11-05 19:00.
    with pytest.raises(ServiceError, match="позже начала события"):
        mk_event("activity", reply_by="2026-11-05 19:01")


def test_reply_by_equal_to_start_is_allowed(conn, org, mk_event):
    event_id = mk_event(reply_by="2026-11-05 19:00")
    assert events.get_event(conn, org, event_id)["reply_by"] == "2026-11-05 19:00"


def test_changed_reply_by_in_the_past_is_rejected(conn, org, mk_event, now):
    event_id = mk_event(reply_by="2026-11-04 18:00")
    day = now.date() + timedelta(days=3)
    with pytest.raises(ServiceError, match="не может быть в прошлом"):
        events.update_event(conn, org, event_id, "Т", day, time(19, 0),
                            reply_by="2026-11-01 18:00", now=now)


def test_editing_after_reply_by_passed_is_not_blocked(conn, org, mk_event, now):
    event_id = mk_event(reply_by="2026-11-04 18:00")
    later = datetime(2026, 11, 4, 20, 0)  # срок уже наступил, событие ещё не началось
    day = now.date() + timedelta(days=3)
    events.update_event(conn, org, event_id, "Новое название", day, time(19, 0), "Зал",
                        reply_by="2026-11-04 18:00", now=later)
    event = events.get_event(conn, org, event_id)
    assert event["title"] == "Новое название" and event["reply_by"] == "2026-11-04 18:00"
    with pytest.raises(ServiceError, match="не может быть в прошлом"):
        events.update_event(conn, org, event_id, "Т", day, time(19, 0),
                            reply_by="2026-11-04 19:00", now=later)


def test_moving_start_before_reply_by_is_rejected(conn, org, mk_event, now):
    event_id = mk_event(reply_by="2026-11-04 18:00")
    with pytest.raises(ServiceError, match="позже начала события"):
        events.update_event(conn, org, event_id, "Т", now.date() + timedelta(days=1), time(19, 0),
                            reply_by="2026-11-04 18:00", now=now)


# ---------- удаление игры с результатом (FR-14) ----------

def test_game_with_result_cannot_be_deleted(conn, org, played_game, now):
    from teamplan import results

    results.save_result(conn, org, played_game, 0, 0, [], now=now)
    with pytest.raises(ServiceError, match="с внесённым результатом удалить нельзя"):
        events.delete_event(conn, org, played_game)
    assert events.get_event(conn, org, played_game)["id"] == played_game


def test_delete_event_removes_its_notifications(conn, org, p1, mk_event, now):
    event_id = mk_event()
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "yes", now=now)
    events.cancel_event(conn, org, event_id, now=now)
    assert conn.execute("SELECT COUNT(*) AS n FROM notifications").fetchone()["n"] == 1
    events.delete_event(conn, org, event_id)
    assert conn.execute("SELECT COUNT(*) AS n FROM notifications").fetchone()["n"] == 0


# ---------- статус для показа (FR-48) ----------

def _status(conn, actor, event_id, now):
    ev = events.get_event(conn, actor, event_id, now=now)
    return ev["display_status"], ev["display_status_label"]


@pytest.mark.parametrize("event_type", ["training", "activity", "game"])
def test_future_event_is_planned(conn, org, mk_event, now, event_type):
    ev = mk_event(event_type)
    assert _status(conn, org, ev, now) == ("planned", "Запланировано")


@pytest.mark.parametrize("event_type", ["training", "activity"])
def test_started_training_or_activity_is_past(conn, org, mk_event, now, event_type):
    ev = mk_event(event_type, days=1, hour=19)
    start = datetime(2026, 11, 3, 19, 0)
    assert _status(conn, org, ev, start - timedelta(minutes=1))[0] == "planned"
    # Ровно в момент начала событие уже началось.
    assert _status(conn, org, ev, start) == ("past", "Прошло")
    assert _status(conn, org, ev, start + timedelta(days=1))[0] == "past"


def test_started_game_with_score_is_played(conn, org, p1, played_game, now):
    from teamplan import results

    results.save_result(conn, org, played_game, 2, 1, [(p1.player_id, None)], now=now)
    assert _status(conn, p1, played_game, now) == ("played", "Сыграна")


def test_started_game_without_score(conn, org, p1, played_game, now):
    assert _status(conn, p1, played_game, now) == ("no_score", "Счёт не внесён")


def test_game_status_at_start_boundary(conn, org, mk_event, now):
    game = mk_event("game", days=1, hour=19)
    start = datetime(2026, 11, 3, 19, 0)
    assert _status(conn, org, game, start - timedelta(seconds=1))[0] == "planned"
    assert _status(conn, org, game, start)[0] == "no_score"


def test_cancelled_event_is_cancelled(conn, org, mk_event, now):
    ev = mk_event("training")
    events.cancel_event(conn, org, ev, now=now)
    # Отмена важнее времени: и после начала событие остаётся «Отменено».
    assert _status(conn, org, ev, now) == ("cancelled", "Отменено")
    assert _status(conn, org, ev, now + timedelta(days=30))[0] == "cancelled"


def test_display_status_in_list(conn, org, mk_event, now):
    ev = mk_event("activity")
    [item] = events.list_events(conn, org, now=now)
    assert item["id"] == ev and item["display_status"] == "planned"
    # В базе по-прежнему хранится только признак отмены.
    assert conn.execute("SELECT status FROM events WHERE id = %s", (ev,)).fetchone()["status"] == "planned"


@pytest.mark.parametrize("status, event_type, started, scored, expected", [
    ("cancelled", "game", True, True, "cancelled"),
    ("planned", "training", False, False, "planned"),
    ("planned", "game", False, True, "planned"),
    ("planned", "training", True, False, "past"),
    ("planned", "activity", True, False, "past"),
    ("planned", "game", True, True, "played"),
    ("planned", "game", True, False, "no_score"),
])
def test_display_status_rules(status, event_type, started, scored, expected):
    event = {"status": status, "type": event_type, "date": "2026-11-02", "time": "12:00"}
    now = datetime(2026, 11, 2, 12, 0) if started else datetime(2026, 11, 2, 11, 59)
    assert events.display_status(event, scored, now) == expected
