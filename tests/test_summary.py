"""Сводные таблицы расписания: итог игры, сводка явки, вкладки по типам (FR-43 – FR-45)."""
import pytest

from teamplan import attendance, events, results, summary
from teamplan.errors import ServiceError


@pytest.mark.parametrize("our, their, outcome", [
    (2, 1, "Победа"), (5, 0, "Победа"),
    (0, 0, "Ничья"), (3, 3, "Ничья"),
    (1, 2, "Поражение"), (0, 4, "Поражение"),
])
def test_game_outcome(our, their, outcome):
    assert summary.game_outcome(our, their) == outcome


def test_game_without_score_has_no_outcome():
    assert summary.game_outcome(None, None) is None
    assert summary.score_text(None, None) == "—"
    assert summary.score_text(2, 1) == "2 : 1"


def test_games_table_score_and_outcome(conn, org, p1, p2, played_game, mk_event, now):
    results.save_result(conn, org, played_game, 2, 1, [(p1.player_id, p2.player_id)], now=now)
    future = mk_event("game", title="Будущая игра")
    rows = summary.games_table(conn, p1, now=now)
    assert [r["id"] for r in rows] == [played_game, future]
    played, upcoming = rows
    assert (played["score"], played["outcome"]) == ("2 : 1", "Победа")
    assert (upcoming["score"], upcoming["outcome"]) == ("—", "—")
    # Статус в таблице — по FR-48.
    assert played["display_status_label"] == "Сыграна"
    assert upcoming["display_status_label"] == "Запланировано"
    assert upcoming["our_score"] is None


def test_games_table_draw_and_loss(conn, org, p1, p2, played_game, now):
    results.save_result(conn, org, played_game, 1, 1, [(p1.player_id, None)], now=now)
    assert summary.games_table(conn, org)[0]["outcome"] == "Ничья"
    results.save_result(conn, org, played_game, 0, 3, now=now)
    assert summary.games_table(conn, org)[0]["outcome"] == "Поражение"


def test_attendance_counts(conn, org, p1, p2, p3, mk_event, now):
    training = mk_event("training")
    other = mk_event("training", days=4)
    attendance.set_attendance(conn, p1, training, p1.player_id, "yes", now=now)
    attendance.set_attendance(conn, p2, training, p2.player_id, "maybe", now=now)
    attendance.set_attendance(conn, p3, other, p3.player_id, "no", now=now)
    rows = summary.attendance_table(conn, org, "training")
    assert [r["id"] for r in rows] == [training, other]
    assert rows[0]["counts"] == {"yes": 1, "maybe": 1, "no": 0, "none": 1}
    assert rows[1]["counts"] == {"yes": 0, "maybe": 0, "no": 1, "none": 2}
    # Сводка в таблице совпадает со сводкой в карточке события (FR-21).
    assert rows[0]["counts"] == attendance.attendance_summary(conn, org, training, now=now)["counts"]


def test_attendance_counts_ignore_unknown_players():
    counts = summary.attendance_counts({1: "yes", 99: "no"}, [1, 2])
    assert counts == {"yes": 1, "maybe": 0, "no": 0, "none": 1}


def test_filter_by_type(conn, org, mk_event):
    game = mk_event("game", days=1)
    training = mk_event("training", days=2)
    activity = mk_event("activity", days=3)
    evs = events.list_events(conn, org)
    assert [e["id"] for e in summary.filter_by_type(evs, None)] == [game, training, activity]
    assert [e["id"] for e in summary.filter_by_type(evs, "game")] == [game]
    assert [e["id"] for e in summary.filter_by_type(evs, "training")] == [training]
    assert [e["id"] for e in summary.filter_by_type(evs, "activity")] == [activity]
    with pytest.raises(ServiceError):
        summary.filter_by_type(evs, "meeting")


def test_tables_only_contain_own_type(conn, org, mk_event):
    mk_event("game", days=1)
    training = mk_event("training", days=2)
    activity = mk_event("activity", days=3)
    assert [r["id"] for r in summary.attendance_table(conn, org, "training")] == [training]
    assert [r["id"] for r in summary.attendance_table(conn, org, "activity")] == [activity]
    with pytest.raises(ServiceError, match="сводка явки не строится"):
        summary.attendance_table(conn, org, "game")


def test_empty_tab(conn, org, mk_event):
    mk_event("training")
    assert summary.games_table(conn, org) == []
    assert summary.attendance_table(conn, org, "activity") == []


def test_cancelled_events_stay_in_order(conn, org, p1, mk_event, now):
    late = mk_event("activity", days=5, hour=10)
    early = mk_event("activity", days=2, hour=18)
    same_day_earlier = mk_event("activity", days=5, hour=9)
    events.cancel_event(conn, org, early, now=now)
    rows = summary.attendance_table(conn, p1, "activity", now=now)
    assert [r["id"] for r in rows] == [early, same_day_earlier, late]
    assert rows[0]["status"] == "cancelled"
    assert [r["display_status_label"] for r in rows] == ["Отменено", "Запланировано", "Запланировано"]

    game = mk_event("game", days=1)
    events.cancel_event(conn, org, game, now=now)
    games = summary.games_table(conn, p1)
    assert [(g["id"], g["status_label"], g["score"]) for g in games] == [(game, "Отменено", "—")]
