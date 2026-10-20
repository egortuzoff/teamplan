"""Уведомления организатору (FR-52 – FR-55, AC-20)."""
import threading
from datetime import datetime, time, timedelta

import psycopg
import pytest

from conftest import notes
from teamplan import attendance, auth, deadlines, events, notifications, results
from teamplan.db import connect, transaction
from teamplan.errors import ServiceError


def _kinds(conn, actor):
    return [n["kind"] for n in notifications.list_notifications(conn, actor)]


# ---------- FR-52: явка игрока ----------

def test_first_yes_does_not_notify(conn, org, p1, mk_event, now):
    event_id = mk_event()
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "yes", now=now)
    assert notes(conn, org) == []


@pytest.mark.parametrize("status, label", [("maybe", "Под вопросом"), ("no", "Не буду")])
def test_first_maybe_or_no_notifies(conn, org, p1, mk_event, now, status, label):
    event_id = mk_event(title="Тренировка")
    attendance.set_attendance(conn, p1, event_id, p1.player_id, status, now=now)
    assert notes(conn, org) == [
        f"Игрок Первый: явка на «Тренировка» 05.11.2026 в 19:00 — «{label}» "
        "(прежний ответ: ответа не было)."
    ]
    assert _kinds(conn, org) == ["attendance"]
    assert notifications.unread_count(conn, org) == 1


def test_change_yes_to_no_notifies_with_both_answers(conn, org, p1, mk_event, now):
    """AC-20: «Буду» → «Не буду» — уведомление с прежним и новым ответом."""
    event_id = mk_event(title="Тренировка")
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "yes", now=now)
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "no", now=now)
    text = notes(conn, org)
    assert len(text) == 1
    assert "Игрок Первый" in text[0] and "05.11.2026 в 19:00" in text[0]
    assert "«Не буду»" in text[0] and "прежний ответ: «Буду»" in text[0]
    assert notifications.list_notifications(conn, org)[0]["event_id"] == event_id


def test_any_change_notifies_and_same_answer_does_not(conn, org, p1, mk_event, now):
    event_id = mk_event()
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "no", now=now)
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "no", now=now)  # без изменения
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "yes", now=now)
    assert len(notes(conn, org)) == 2
    assert "«Буду» (прежний ответ: «Не буду»)" in notes(conn, org)[0]


def test_event_type_is_named(conn, org, p1, mk_event, now):
    event_id = mk_event("activity", title="Выезд на базу")
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "maybe", now=now)
    assert notes(conn, org)[0].startswith(
        "Игрок Первый: явка на Мероприятие «Выезд на базу» 05.11.2026 в 19:00"
    )


def test_organizer_mark_does_not_notify(conn, org, p1, mk_event, now):
    """FR-35: отметка, которую ставит организатор за игрока, без уведомления."""
    event_id = mk_event()
    attendance.set_attendance(conn, org, event_id, p1.player_id, "no", now=now)
    attendance.set_attendance(conn, org, event_id, p1.player_id, "maybe", now=now)
    assert notes(conn, org) == []
    assert notes(conn, p1) == []


def test_player_does_not_see_organizer_notifications(conn, org, p1, p2, mk_event, now):
    event_id = mk_event()
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "no", now=now)
    assert notes(conn, p1) == [] and notes(conn, p2) == []


def test_rejected_mark_creates_no_notification(conn, org, p1, mk_event, now):
    event_id = mk_event()
    events.cancel_event(conn, org, event_id, now=now)
    with pytest.raises(ServiceError):
        attendance.set_attendance(conn, p1, event_id, p1.player_id, "no", now=now)
    assert notes(conn, org) == []


# ---------- FR-53: срок «ответить до» ----------

def _reply_event(mk_event, now, **kwargs):
    """Тренировка через 3 дня со сроком ответа через сутки от now."""
    return mk_event(reply_by=(now + timedelta(days=1)).strftime("%Y-%m-%d %H:%M"), **kwargs)


def test_overdue_reply_one_notification_with_missing(conn, org, p1, p2, p3, mk_event, now):
    """AC-20: после срока — одно уведомление со списком; повторные проверки дубликатов не дают."""
    event_id = _reply_event(mk_event, now, title="Тренировка")
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "yes", now=now)
    deadline = now + timedelta(days=1)

    # Ровно в срок он ещё не прошёл — как подсветка не ответивших (FR-22).
    assert deadlines.check_deadlines(conn, now=deadline) == 0
    later = deadline + timedelta(minutes=1)
    assert deadlines.check_deadlines(conn, now=later) == 1
    for _ in range(3):
        assert deadlines.check_deadlines(conn, now=later + timedelta(hours=1)) == 0
    assert notes(conn, org) == [
        "Срок ответа прошёл: «Тренировка» 05.11.2026 в 19:00. "
        "Не ответили (2): Игрок Второй, Игрок Третий."
    ]
    assert _kinds(conn, org) == ["reply_overdue"]
    assert notes(conn, p2) == []


def test_overdue_reply_once_even_after_new_answers(conn, org, p1, p2, mk_event, now):
    event_id = _reply_event(mk_event, now)
    later = now + timedelta(days=1, minutes=1)
    deadlines.check_deadlines(conn, now=later)
    attendance.set_attendance(conn, org, event_id, p1.player_id, "yes", now=later)
    deadlines.check_deadlines(conn, now=later)
    assert _kinds(conn, org) == ["reply_overdue"]


def test_overdue_reply_all_answered_no_notification(conn, org, p1, p2, mk_event, now):
    event_id = _reply_event(mk_event, now)
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "yes", now=now)
    attendance.set_attendance(conn, org, event_id, p2.player_id, "yes", now=now)
    assert deadlines.check_deadlines(conn, now=now + timedelta(days=2)) == 0
    assert notes(conn, org) == []


def test_before_deadline_no_notification(conn, org, p1, mk_event, now):
    _reply_event(mk_event, now)
    assert deadlines.check_deadlines(conn, now=now) == 0
    assert notes(conn, org) == []


# ---------- FR-54: игра без счёта ----------

def test_game_without_score_once_and_not_after_score(conn, org, p1, p2, played_game, now):
    """AC-20: одно уведомление «внесите счёт», после внесения счёта новых нет."""
    assert deadlines.check_deadlines(conn, now=now) == 1
    assert deadlines.check_deadlines(conn, now=now) == 0
    assert _kinds(conn, org) == ["no_score"]
    item = notifications.list_notifications(conn, org)[0]
    assert item["event_id"] == played_game  # «Открыть» ведёт в карточку игры
    assert item["text"].startswith("Внесите счёт: Игра «Прошедшая игра» 31.10.2026 в 19:00")
    assert "no_score" not in _kinds(conn, p1)

    results.save_result(conn, org, played_game, 1, 0, [(p1.player_id, None)], now=now)
    deadlines.check_deadlines(conn, now=now + timedelta(days=1))
    assert _kinds(conn, org) == ["no_score"]


def test_game_starts_exactly_now_counts_as_started(conn, org, mk_event, now):
    """Граница как у статуса «Счёт не внесён» (FR-48): с момента начала."""
    game = mk_event("game", days=1, hour=19)
    start = datetime.combine(now.date() + timedelta(days=1), time(19, 0))
    assert deadlines.check_deadlines(conn, now=start - timedelta(minutes=1)) == 0
    assert deadlines.check_deadlines(conn, now=start) == 1
    assert notifications.list_notifications(conn, org)[0]["event_id"] == game


def test_game_with_score_no_notification(conn, org, p1, played_game, now):
    results.save_result(conn, org, played_game, 1, 0, [(p1.player_id, None)], now=now)
    assert deadlines.check_deadlines(conn, now=now) == 0


def test_future_game_no_notification(conn, org, mk_event, now):
    mk_event("game")
    assert deadlines.check_deadlines(conn, now=now) == 0


# ---------- отменённое событие ----------

def test_cancelled_events_no_notifications(conn, org, p1, mk_event, now):
    training = _reply_event(mk_event, now)
    game = mk_event("game", days=1)
    events.cancel_event(conn, org, training, now=now)
    events.cancel_event(conn, org, game, now=now)
    assert deadlines.check_deadlines(conn, now=now + timedelta(days=5)) == 0
    assert notes(conn, org) == []


# ---------- FR-55: дубликаты исключает база ----------

def test_database_rejects_duplicate(conn, org, played_game, now):
    deadlines.check_deadlines(conn, now=now)
    user_id = conn.execute("SELECT id FROM users WHERE login = 'trener'").fetchone()["id"]
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO notifications (user_id, event_id, kind, text, created_at) "
            "VALUES (%s, %s, 'no_score', 'дубль', 't')", (user_id, played_game),
        )


def test_concurrent_checks_create_one_notification(database_url, now):
    """Несколько страниц проверяют сроки одновременно — запись одна."""
    path = database_url
    conn = connect(path)
    auth.create_organizer(conn, "trener", "pass-org")
    org = auth.login(conn, "trener", "pass-org")
    events.create_event(conn, org, "game", "Игра", now.date() - timedelta(days=1), time(19, 0),
                        now=now - timedelta(days=7))
    conn.close()

    barrier = threading.Barrier(4)
    errors = []

    def page():
        c = connect(path)
        try:
            barrier.wait()
            deadlines.check_deadlines(c, now=now)
        except Exception as exc:
            errors.append(exc)
        finally:
            c.close()

    threads = [threading.Thread(target=page) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    conn = connect(path)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM notifications").fetchone()["n"] == 1
    finally:
        conn.close()


def test_unique_index_blocks_duplicate_from_parallel_transaction(database_url, now):
    """FR-55 без очереди проверок: вторая вставка ждёт первую транзакцию и пропускается.

    Две транзакции одновременно вставляют «внесите счёт» на одну игру. Пока первая
    не завершена, вторая ждёт на уникальном индексе notifications_once; после
    фиксации первой вторая ничего не создаёт (ON CONFLICT ... DO NOTHING).
    """
    first = connect(database_url)
    second = connect(database_url)
    try:
        auth.create_organizer(first, "trener", "pass-org")
        org = auth.login(first, "trener", "pass-org")
        game = events.create_event(first, org, "game", "Игра", now.date() - timedelta(days=1),
                                   time(19, 0), now=now - timedelta(days=7))
        created = {}

        def insert_second():
            with transaction(second):
                created["second"] = notifications.notify_organizers(
                    second, game, "no_score", "Внесите счёт", now)

        with transaction(first):
            created["first"] = notifications.notify_organizers(
                first, game, "no_score", "Внесите счёт", now)
            worker = threading.Thread(target=insert_second)
            worker.start()
            worker.join(timeout=1)
            assert worker.is_alive()  # вторая транзакция ждёт первую
        worker.join(timeout=10)
        assert not worker.is_alive()
        assert created == {"first": 1, "second": 0}
        count = first.execute("SELECT COUNT(*) AS n FROM notifications").fetchone()["n"]
        assert count == 1
    finally:
        first.close()
        second.close()
