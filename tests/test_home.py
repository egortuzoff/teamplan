"""Главный экран: подборки данных для игрока и организатора (FR-49 – FR-51)."""
from datetime import time, timedelta

import pytest

from teamplan import attendance, events, home, notifications, results, squads
from teamplan.errors import ServiceError


@pytest.mark.parametrize("n, form", [
    (1, "день"), (2, "дня"), (4, "дня"), (5, "дней"), (11, "дней"), (12, "дней"),
    (14, "дней"), (21, "день"), (22, "дня"), (25, "дней"), (101, "день"), (111, "дней"),
])
def test_plural(n, form):
    assert home.plural(n, ("день", "дня", "дней")) == form


@pytest.mark.parametrize("days, text", [
    (0, "сегодня"), (1, "завтра"), (2, "через 2 дня"), (5, "через 5 дней"), (21, "через 21 день"),
])
def test_days_until_text(days, text):
    assert home.days_until_text(days) == text


def _past_game(conn, org, now, days_ago, squad, title="Прошлая игра"):
    """Игра в прошлом с опубликованным составом (создаётся «неделю назад»)."""
    before = now - timedelta(days=days_ago + 7)
    game = events.create_event(
        conn, org, "game", title, now.date() - timedelta(days=days_ago), time(19, 0),
        now=before,
    )
    squads.save_squad(conn, org, game, squad, now=before)
    squads.publish_squad(conn, org, game, now=before)
    return game


# ---------- пустые блоки ----------

def test_player_home_without_events(conn, org, p1, now):
    data = home.player_home(conn, p1, now=now)
    assert data["next_event"] is None
    assert data["last_match"] is None
    assert data["week"] == []
    assert data["stats"] == {"games": 0, "goals": 0, "assists": 0}


def test_organizer_home_without_events(conn, org, now):
    data = home.organizer_home(conn, org, now=now)
    assert data == {"needs_action": None, "no_score": [], "week_attendance": []}


def test_organizer_without_player_has_no_stats(conn, org, now):
    assert home.player_home(conn, org, now=now)["stats"] is None


def test_home_requires_login(conn, now):
    with pytest.raises(ServiceError):
        home.player_home(conn, None, now=now)
    with pytest.raises(ServiceError):
        home.organizer_home(conn, None, now=now)


def test_organizer_home_is_for_organizer_only(conn, org, p1, now):
    with pytest.raises(ServiceError, match="только организатору"):
        home.organizer_home(conn, p1, now=now)


# ---------- игрок: следующее событие ----------

def test_next_event_skips_cancelled_and_past(conn, org, p1, mk_event, now):
    cancelled = mk_event("training", days=1, title="Отменённая")
    events.cancel_event(conn, org, cancelled, now=now)
    later = mk_event("activity", days=4, title="Разбор матча", place="Зал")
    _past_game(conn, org, now, 2, [p1.player_id])
    nxt = home.player_home(conn, p1, now=now)["next_event"]
    assert nxt["id"] == later
    assert nxt["days_until"] == 4
    assert nxt["place"] == "Зал"


def test_next_event_today_counts_as_zero_days(conn, org, p1, now):
    ev = events.create_event(conn, org, "training", "Сегодня", now.date(), time(20, 0), now=now)
    nxt = home.player_home(conn, p1, now=now)["next_event"]
    assert (nxt["id"], nxt["days_until"]) == (ev, 0)


def test_next_game_marks_player_in_published_squad(conn, org, p1, p2, mk_event, now):
    game = mk_event("game", days=2, title="Игра", opponent="Тестовые ястребы")
    squads.save_squad(conn, org, game, [p1.player_id], now=now)
    squads.publish_squad(conn, org, game, now=now)
    first = home.player_home(conn, p1, now=now)["next_event"]
    assert first["in_squad"] is True
    assert first["opponent"] == "Тестовые ястребы"
    second = home.player_home(conn, p2, now=now)["next_event"]
    assert second["squad_published"] is True
    assert second["in_squad"] is False


def test_player_does_not_see_draft_squad(conn, org, p1, mk_event, now):
    game = mk_event("game", days=2)
    squads.save_squad(conn, org, game, [p1.player_id], now=now)
    nxt = home.player_home(conn, p1, now=now)["next_event"]
    assert nxt["squad_published"] is False
    assert nxt["in_squad"] is False
    assert nxt["squad_players"] == []  # черновик игроку не показывается (FR-25)


# ---------- игрок: последний матч и статистика ----------

def test_last_match_is_latest_played_game(conn, org, p1, p2, now):
    older = _past_game(conn, org, now, 10, [p1.player_id, p2.player_id], "Старая игра")
    newer = _past_game(conn, org, now, 3, [p1.player_id, p2.player_id], "Новая игра")
    _past_game(conn, org, now, 1, [p1.player_id], "Игра без счёта")
    results.save_result(conn, org, older, 0, 3, now=now)
    results.save_result(
        conn, org, newer, 2, 1,
        [(p1.player_id, p2.player_id), (p1.player_id, None)], now=now,
    )
    last = home.player_home(conn, p1, now=now)["last_match"]
    assert last["id"] == newer
    assert (last["our_score"], last["their_score"], last["outcome"]) == (2, 1, "Победа")
    assert [(g["scorer_name"], g["assist_name"]) for g in last["goals"]] == [
        ("Игрок Первый", "Игрок Второй"), ("Игрок Первый", None),
    ]


def test_no_last_match_without_played_games(conn, org, p1, now):
    _past_game(conn, org, now, 1, [p1.player_id])  # счёт не внесён
    assert home.player_home(conn, p1, now=now)["last_match"] is None


def test_my_stats_match_statistics_table(conn, org, p1, p2, p3, now):
    game = _past_game(conn, org, now, 3, [p1.player_id, p2.player_id])
    results.save_result(
        conn, org, game, 2, 1,
        [(p1.player_id, p2.player_id), (p1.player_id, None)], now=now,
    )
    assert home.player_home(conn, p1, now=now)["stats"] == {"games": 1, "goals": 2, "assists": 0}
    assert home.player_home(conn, p2, now=now)["stats"] == {"games": 1, "goals": 0, "assists": 1}
    assert home.player_home(conn, p3, now=now)["stats"] == {"games": 0, "goals": 0, "assists": 0}


# ---------- игрок: неделя и явка из карточки ----------

def test_week_contains_only_next_seven_days(conn, org, p1, mk_event, now):
    in_week = mk_event("training", days=6, title="В неделе")
    game = mk_event("game", days=2, title="Игра")
    mk_event("activity", days=8, title="Позже")
    _past_game(conn, org, now, 1, [p1.player_id])
    week = home.player_home(conn, p1, now=now)["week"]
    assert [e["id"] for e in week] == [game, in_week]
    assert week[0]["squad_published"] is False
    assert week[1]["can_mark"] is True
    assert week[1]["my_mark"] is None


def test_week_shows_cancelled_event_without_marking(conn, org, p1, mk_event, now):
    ev = mk_event("training", days=2)
    events.cancel_event(conn, org, ev, now=now)
    item = home.player_home(conn, p1, now=now)["week"][0]
    assert item["display_status"] == "cancelled"
    assert item["can_mark"] is False


def test_mark_from_week_card_is_visible_in_event_card(conn, org, p1, p2, mk_event, now):
    """Отметка с главного экрана — та же функция, что и в карточке события (FR-51)."""
    ev = mk_event("training", days=2)
    attendance.set_attendance(conn, p1, ev, p1.player_id, "maybe", now=now)
    assert home.player_home(conn, p1, now=now)["week"][0]["my_mark"] == "maybe"
    summary = attendance.attendance_summary(conn, p1, ev, now=now)
    assert summary["marks"][p1.player_id] == "maybe"
    # Новая отметка заменяет прежнюю (FR-18).
    attendance.set_attendance(conn, p1, ev, p1.player_id, "yes", now=now)
    assert home.player_home(conn, p1, now=now)["week"][0]["my_mark"] == "yes"
    # Чужая отметка не видна как своя.
    assert home.player_home(conn, p2, now=now)["week"][0]["my_mark"] is None


def test_week_marking_keeps_the_same_rights(conn, org, p1, p2, mk_event, now):
    ev = mk_event("training", days=2)
    with pytest.raises(ServiceError, match="только за себя"):
        attendance.set_attendance(conn, p1, ev, p2.player_id, "yes", now=now)
    game = mk_event("game", days=2)
    with pytest.raises(ServiceError, match="На игры явка не отмечается"):
        attendance.set_attendance(conn, p1, game, p1.player_id, "yes", now=now)
    later = now + timedelta(days=3)  # событие уже началось (FR-19)
    with pytest.raises(ServiceError, match="уже началось"):
        attendance.set_attendance(conn, p1, ev, p1.player_id, "no", now=later)


# ---------- организатор ----------

def test_needs_action_is_nearest_game_with_unpublished_squad(conn, org, p1, p2, mk_event, now):
    published = mk_event("game", days=1, title="Опубликован")
    squads.save_squad(conn, org, published, [p1.player_id], now=now)
    squads.publish_squad(conn, org, published, now=now)
    cancelled = mk_event("game", days=2, title="Отменена")
    events.cancel_event(conn, org, cancelled, now=now)
    _past_game(conn, org, now, 1, [p1.player_id])
    draft = mk_event("game", days=5, title="Черновик", opponent="Тестовые медведи")
    squads.save_squad(conn, org, draft, [p2.player_id, p1.player_id], now=now)
    mk_event("game", days=9, title="Без состава")
    action = home.organizer_home(conn, org, now=now)["needs_action"]
    assert action["id"] == draft
    assert action["squad_state"] == "draft"
    assert [p["name"] for p in action["squad_players"]] == ["Игрок Второй", "Игрок Первый"]
    assert action["days_until"] == 5


def test_game_without_squad_needs_action(conn, org, mk_event, now):
    game = mk_event("game", days=3)
    action = home.organizer_home(conn, org, now=now)["needs_action"]
    assert (action["id"], action["squad_state"], action["squad_players"]) == (game, "none", [])


def test_publish_from_home_uses_squad_rules(conn, org, p1, mk_event, now):
    """«Опубликовать состав» — та же функция и проверки прав, что в карточке (FR-51)."""
    game = mk_event("game", days=3)
    with pytest.raises(ServiceError, match="Сначала сохраните состав"):
        squads.publish_squad(conn, org, game, now=now)
    squads.save_squad(conn, org, game, [p1.player_id], now=now)
    with pytest.raises(ServiceError, match="только организатору"):
        squads.publish_squad(conn, p1, game, now=now)
    squads.publish_squad(conn, org, game, now=now)
    assert home.organizer_home(conn, org, now=now)["needs_action"] is None
    assert notifications.unread_count(conn, p1) == 1
    assert home.player_home(conn, p1, now=now)["next_event"]["in_squad"] is True


def test_no_score_lists_past_games_without_result(conn, org, p1, mk_event, now):
    first = _past_game(conn, org, now, 5, [p1.player_id], "Первая")
    second = _past_game(conn, org, now, 1, [p1.player_id], "Вторая")
    mk_event("game", days=2, title="Будущая")
    data = home.organizer_home(conn, org, now=now)
    assert [g["id"] for g in data["no_score"]] == [first, second]
    results.save_result(conn, org, first, 1, 0, [(p1.player_id, None)], now=now)
    assert [g["id"] for g in home.organizer_home(conn, org, now=now)["no_score"]] == [second]


def test_week_attendance_counts_and_missing(conn, org, p1, p2, p3, mk_event, now):
    training = mk_event("training", days=2, title="Тренировка")
    activity = mk_event("activity", days=6, title="Выезд")
    mk_event("game", days=3, title="Игра")
    mk_event("training", days=8, title="Позже")
    cancelled = mk_event("training", days=4, title="Отменена")
    events.cancel_event(conn, org, cancelled, now=now)
    attendance.set_attendance(conn, p1, training, p1.player_id, "yes", now=now)
    attendance.set_attendance(conn, org, training, p2.player_id, "no", now=now)
    week = home.organizer_home(conn, org, now=now)["week_attendance"]
    assert [e["id"] for e in week] == [training, activity]
    assert week[0]["counts"] == {"yes": 1, "maybe": 0, "no": 1, "none": 1}
    assert week[0]["missing"] == ["Игрок Третий"]
    assert week[1]["missing"] == ["Игрок Второй", "Игрок Первый", "Игрок Третий"]
    # Те же числа, что в сводке карточки события (FR-21).
    assert week[0]["counts"] == attendance.attendance_summary(conn, org, training, now=now)["counts"]


def test_week_attendance_highlights_missing_after_deadline(conn, org, p1, mk_event, now):
    reply_by = (now + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
    mk_event("training", days=2, reply_by=reply_by)
    later = now + timedelta(hours=3)
    assert home.organizer_home(conn, org, now=now)["week_attendance"][0]["highlight_missing"] is False
    assert home.organizer_home(conn, org, now=later)["week_attendance"][0]["highlight_missing"] is True
