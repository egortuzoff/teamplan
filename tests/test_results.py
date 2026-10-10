"""Результат игры, голы, передачи (FR-27 – FR-31) и статистика (FR-32, FR-33)."""
from datetime import datetime

import pytest

from teamplan import events, results, squads
from teamplan.errors import ServiceError


def test_organizer_enters_score_and_goals(conn, org, p1, p2, played_game, now):
    warning = results.save_result(
        conn, org, played_game, 2, 1,
        [(p1.player_id, p2.player_id), (p2.player_id, None)], now=now,
    )
    assert warning is None
    result = results.get_result(conn, p1, played_game)
    assert (result["our_score"], result["their_score"]) == (2, 1)
    assert [(g["scorer_name"], g["assist_name"]) for g in result["goals"]] == [
        ("Игрок Первый", "Игрок Второй"), ("Игрок Второй", None),
    ]
    assert result["created_at"] and result["updated_at"]


def test_result_requires_started_game(conn, org, p1, mk_event, now):
    game = mk_event("game")
    squads.save_squad(conn, org, game, [p1.player_id], now=now)
    squads.publish_squad(conn, org, game, now=now)
    with pytest.raises(ServiceError, match="после начала игры"):
        results.save_result(conn, org, game, 1, 0, now=now)
    # Начало игры 2026-11-05 19:00: с этого момента результат вносится.
    results.save_result(conn, org, game, 1, 0, [(p1.player_id, None)],
                        now=datetime(2026, 11, 5, 19, 0))


def test_result_requires_published_squad(conn, org, p1, now):
    week_ago = datetime(2026, 10, 26, 12, 0)
    game = events.create_event(conn, org, "game", "Игра", "2026-10-31", "19:00", now=week_ago)
    with pytest.raises(ServiceError, match="опубликованным составом"):
        results.save_result(conn, org, game, 1, 0, now=now)
    squads.save_squad(conn, org, game, [p1.player_id], now=week_ago)
    with pytest.raises(ServiceError, match="опубликованным составом"):
        results.save_result(conn, org, game, 1, 0, now=now)


def test_result_only_for_games(conn, org, mk_event, now):
    training = mk_event()
    with pytest.raises(ServiceError, match="только для игр"):
        results.save_result(conn, org, training, 1, 0, now=now)


def test_player_cannot_enter_result(conn, org, p1, played_game, now):
    with pytest.raises(ServiceError, match="только организатору"):
        results.save_result(conn, p1, played_game, 1, 0, [(p1.player_id, None)], now=now)
    assert results.get_result(conn, p1, played_game) is None


@pytest.mark.parametrize("score", [-1, 100, "два", None, 1.5, True])
def test_wrong_score_is_rejected(conn, org, played_game, now, score):
    with pytest.raises(ServiceError, match="Счёт нашей команды"):
        results.save_result(conn, org, played_game, score, 0, now=now)
    with pytest.raises(ServiceError, match="Счёт соперника"):
        results.save_result(conn, org, played_game, 0, score, now=now)


def test_scorer_must_be_in_squad(conn, org, p1, p3, played_game, now):
    with pytest.raises(ServiceError, match="автор должен входить в состав"):
        results.save_result(conn, org, played_game, 1, 0, [(p3.player_id, None)], now=now)
    with pytest.raises(ServiceError, match="автор должен входить в состав"):
        results.save_result(conn, org, played_game, 1, 0, [(None, p1.player_id)], now=now)


def test_assist_must_be_in_squad(conn, org, p1, p3, played_game, now):
    with pytest.raises(ServiceError, match="передающий должен входить в состав"):
        results.save_result(conn, org, played_game, 1, 0, [(p1.player_id, p3.player_id)], now=now)


def test_scorer_and_assist_must_differ(conn, org, p1, played_game, now):
    with pytest.raises(ServiceError, match="не могут совпадать"):
        results.save_result(conn, org, played_game, 1, 0, [(p1.player_id, p1.player_id)], now=now)


def test_rejected_goal_saves_nothing(conn, org, p1, p3, played_game, now):
    with pytest.raises(ServiceError):
        results.save_result(
            conn, org, played_game, 2, 0, [(p1.player_id, None), (p3.player_id, None)], now=now
        )
    assert results.get_result(conn, org, played_game) is None


def test_goal_count_mismatch_warns_and_saves(conn, org, p1, played_game, now):
    warning = results.save_result(conn, org, played_game, 3, 0, [(p1.player_id, None)], now=now)
    assert "голов в списке 1, а счёт нашей команды 3" in warning
    result = results.get_result(conn, org, played_game)
    assert result["our_score"] == 3 and len(result["goals"]) == 1
    assert result["warning"] == warning


def test_result_can_be_edited(conn, org, p1, p2, played_game, now):
    results.save_result(conn, org, played_game, 1, 0, [(p1.player_id, None)], now=now)
    results.save_result(conn, org, played_game, 2, 2,
                        [(p2.player_id, p1.player_id), (p2.player_id, None)], now=now)
    result = results.get_result(conn, org, played_game)
    assert (result["our_score"], result["their_score"]) == (2, 2)
    assert [g["scorer_id"] for g in result["goals"]] == [p2.player_id, p2.player_id]
    assert conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0] == 2


def test_cancelled_game_gets_no_result(conn, org, p1, mk_event, now):
    game = mk_event("game")
    squads.save_squad(conn, org, game, [p1.player_id], now=now)
    squads.publish_squad(conn, org, game, now=now)
    events.cancel_event(conn, org, game, now=now)
    with pytest.raises(ServiceError, match="отменена"):
        results.save_result(conn, org, game, 1, 0, now=datetime(2026, 11, 6, 12, 0))


def test_player_with_goal_cannot_be_removed_from_squad(conn, org, p1, p2, played_game, now):
    results.save_result(conn, org, played_game, 1, 0, [(p1.player_id, None)], now=now)
    with pytest.raises(ServiceError, match="с голом или передачей"):
        squads.save_squad(conn, org, played_game, [p2.player_id], now=now)
    squads.save_squad(conn, org, played_game, [p1.player_id], now=now)


# ---------- статистика ----------

def test_stats_empty_without_scores(conn, org, p1, played_game):
    assert results.player_stats(conn, org) == []


def test_stats_match_manual_count(conn, org, p1, p2, p3, played_game, mk_event, now):
    results.save_result(
        conn, org, played_game, 2, 1,
        [(p1.player_id, p2.player_id), (p1.player_id, None)], now=now,
    )
    # Будущая игра с составом, но без счёта, в «игры» не засчитывается (FR-33).
    future = mk_event("game")
    squads.save_squad(conn, org, future, [p1.player_id, p3.player_id], now=now)
    squads.publish_squad(conn, org, future, now=now)

    table = {r["name"]: (r["games"], r["goals"], r["assists"])
             for r in results.player_stats(conn, p3)}
    assert table == {
        "Игрок Первый": (1, 2, 0),
        "Игрок Второй": (1, 0, 1),
        "Игрок Третий": (0, 0, 0),
    }


def test_game_with_zero_score_counts_as_played(conn, org, p1, p2, played_game, now):
    results.save_result(conn, org, played_game, 0, 3, [], now=now)
    games = {r["name"]: r["games"] for r in results.player_stats(conn, org)}
    assert games == {"Игрок Первый": 1, "Игрок Второй": 1}
