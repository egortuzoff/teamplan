"""Лента уведомлений (FR-34 – FR-40, п. 4.5)."""
from datetime import time, timedelta

import pytest

from conftest import notes
from teamplan import attendance, auth, events, notifications, results, squads
from teamplan.errors import ServiceError


def _day(now, days=3):
    return now.date() + timedelta(days=days)


@pytest.mark.parametrize("change", [
    {"date_days": 4}, {"hour": 20}, {"gather": time(18, 0)}, {"place": "Другой зал"},
])
def test_move_notifies_players_with_marks(conn, org, p1, p2, p3, mk_event, now, change):
    event_id = mk_event(place="Зал", gather_time=time(18, 30))
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "yes", now=now)
    attendance.set_attendance(conn, p2, event_id, p2.player_id, "no", now=now)
    events.update_event(
        conn, org, event_id, "Событие", _day(now, change.get("date_days", 3)),
        time(change.get("hour", 19), 0), change.get("place", "Зал"),
        gather_time=change.get("gather", time(18, 30)), now=now,
    )
    assert len(notes(conn, p1)) == 1 and notes(conn, p1)[0].startswith("Перенос")
    assert len(notes(conn, p2)) == 1
    assert notes(conn, p3) == []  # без отметки явки уведомления нет
    # Отметки явки сохранены (AC-9).
    assert attendance.attendance_summary(conn, org, event_id, now=now)["marks"] == {
        p1.player_id: "yes", p2.player_id: "no",
    }


def test_move_text_names_the_change(conn, org, p1, mk_event, now):
    event_id = mk_event(gather_time=time(18, 30))
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "yes", now=now)
    events.update_event(conn, org, event_id, "Событие", _day(now), time(19, 0),
                        gather_time=time(18, 15), now=now)
    assert "время сбора 18:30 → 18:15" in notes(conn, p1)[0]


def test_title_or_reply_by_change_is_not_a_move(conn, org, p1, mk_event, now):
    event_id = mk_event()
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "yes", now=now)
    events.update_event(conn, org, event_id, "Другое название", _day(now), time(19, 0),
                        reply_by="2026-11-04 18:00", now=now)
    assert notes(conn, p1) == []


def test_game_move_notifies_published_squad_only(conn, org, p1, p2, mk_event, now):
    game = mk_event("game")
    squads.save_squad(conn, org, game, [p1.player_id], now=now)
    events.update_event(conn, org, game, "Игра", _day(now), time(20, 0), now=now)
    assert notes(conn, p1) == []  # черновик игрокам не объявлен
    squads.publish_squad(conn, org, game, now=now)
    events.update_event(conn, org, game, "Игра", _day(now), time(21, 0), now=now)
    assert notes(conn, p1)[0].startswith("Перенос")
    assert notes(conn, p2) == []


def test_cancel_notifies_players(conn, org, p1, p2, mk_event, now):
    training = mk_event()
    attendance.set_attendance(conn, p1, training, p1.player_id, "maybe", now=now)
    game = mk_event("game")
    squads.save_squad(conn, org, game, [p2.player_id], now=now)
    squads.publish_squad(conn, org, game, now=now)
    events.cancel_event(conn, org, training, now=now)
    events.cancel_event(conn, org, game, now=now)
    assert [t.startswith("Отменено") for t in notes(conn, p1)] == [True]
    assert notes(conn, p2)[0].startswith("Отменено")


def test_publish_notifies_squad(conn, org, p1, p2, p3, mk_event, now):
    game = mk_event("game")
    squads.save_squad(conn, org, game, [p1.player_id, p2.player_id], now=now)
    assert notes(conn, p1) == []  # черновик — без уведомлений
    squads.publish_squad(conn, org, game, now=now)
    assert "Опубликован состав" in notes(conn, p1)[0]
    assert "Опубликован состав" in notes(conn, p2)[0]
    assert notes(conn, p3) == []


def test_squad_change_notifies_old_and_new_players(conn, org, p1, p2, p3, mk_event, now):
    game = mk_event("game")
    squads.save_squad(conn, org, game, [p1.player_id, p2.player_id], now=now)
    squads.publish_squad(conn, org, game, now=now)
    squads.save_squad(conn, org, game, [p2.player_id, p3.player_id], now=now)
    assert "больше не в составе" in notes(conn, p1)[0]
    assert "по-прежнему в составе" in notes(conn, p2)[0]
    assert "включены в состав" in notes(conn, p3)[0]
    # Сохранение без изменений состава уведомлений не создаёт.
    squads.save_squad(conn, org, game, [p3.player_id, p2.player_id], now=now)
    assert len(notes(conn, p3)) == 1


def test_result_notifies_squad(conn, org, p1, p2, p3, played_game, now):
    before = len(notes(conn, p1))
    results.save_result(conn, org, played_game, 1, 0, [(p1.player_id, None)], now=now)
    assert notes(conn, p1)[0] == (
        "Внесён результат игры «Прошедшая игра» 31.10.2026 в 19:00: 1:0."
    )
    results.save_result(conn, org, played_game, 2, 0,
                        [(p1.player_id, None), (p2.player_id, None)], now=now)
    assert notes(conn, p2)[0].startswith("Изменён результат игры")
    assert len(notes(conn, p1)) == before + 2
    assert notes(conn, p3) == []
    # Повторное сохранение того же результата нового уведомления не создаёт.
    results.save_result(conn, org, played_game, 2, 0,
                        [(p1.player_id, None), (p2.player_id, None)], now=now)
    assert len(notes(conn, p1)) == before + 2


def test_organizer_gets_no_notifications_about_own_actions(conn, mk_event, now):
    auth.create_organizer(conn, "boss", "pass-boss", player_name="Тренер-игрок")
    boss = auth.login(conn, "boss", "pass-boss")
    game = mk_event("game")
    squads.save_squad(conn, boss, game, [boss.player_id], now=now)
    squads.publish_squad(conn, boss, game, now=now)
    events.cancel_event(conn, boss, game, now=now)
    assert notes(conn, boss) == []
    assert notifications.unread_count(conn, boss) == 0


def test_delete_creates_no_notifications(conn, org, p1, mk_event, now):
    event_id = mk_event()
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "yes", now=now)
    events.delete_event(conn, org, event_id)
    assert notes(conn, p1) == []


def test_player_sees_only_own_notifications(conn, org, p1, p2, mk_event, now):
    game = mk_event("game")
    squads.save_squad(conn, org, game, [p1.player_id], now=now)
    squads.publish_squad(conn, org, game, now=now)
    assert len(notes(conn, p1)) == 1
    assert notes(conn, p2) == [] and notes(conn, org) == []
    own = notifications.list_notifications(conn, p1)[0]
    with pytest.raises(ServiceError, match="не найдено"):
        notifications.mark_read(conn, p2, own["id"])
    assert notifications.unread_count(conn, p1) == 1


def test_open_marks_read_and_unread_count_drops(conn, org, p1, mk_event, now):
    for days in (3, 4):
        game = mk_event("game", days=days)
        squads.save_squad(conn, org, game, [p1.player_id], now=now)
        squads.publish_squad(conn, org, game, now=now)
    assert notifications.unread_count(conn, p1) == 2
    first = notifications.list_notifications(conn, p1)[0]
    assert first["is_read"] is False
    notifications.mark_read(conn, p1, first["id"], now=now)
    assert notifications.unread_count(conn, p1) == 1
    feed = notifications.list_notifications(conn, p1)
    assert [n["is_read"] for n in feed] == [True, False]
    notifications.mark_read(conn, p1, first["id"], now=now)  # повторное открытие безопасно
    notifications.mark_all_read(conn, p1, now=now)
    assert notifications.unread_count(conn, p1) == 0


def test_feed_shows_last_50(conn, org, p1, mk_event, now):
    event_id = mk_event()
    attendance.set_attendance(conn, p1, event_id, p1.player_id, "yes", now=now)
    for minute in range(55):
        events.update_event(conn, org, event_id, "Событие", _day(now), time(19, 0),
                            gather_time=time(18, minute % 60), now=now)
    feed = notifications.list_notifications(conn, p1)
    assert len(feed) == 50
    assert feed[0]["id"] > feed[-1]["id"]  # новые сверху
    assert notifications.unread_count(conn, p1) == 55


def test_unauthenticated_access_is_rejected(conn):
    with pytest.raises(ServiceError, match="Сессия"):
        notifications.list_notifications(conn, None)
