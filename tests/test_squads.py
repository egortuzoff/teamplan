import pytest

from teamplan import events, squads
from teamplan.errors import ServiceError


def test_draft_is_invisible_to_players_but_visible_to_organizer(conn, org, p1, p2, mk_event):
    game = mk_event("game")
    squads.save_squad(conn, org, game, [p1.player_id])
    for_player = squads.get_squad(conn, p2, game)
    for_organizer = squads.get_squad(conn, org, game)
    assert for_player["state"] == "none" and for_player["players"] == []
    assert for_organizer["state"] == "draft"
    assert [p["id"] for p in for_organizer["players"]] == [p1.player_id]


def test_published_squad_is_visible_to_players(conn, org, p1, p2, mk_event):
    game = mk_event("game")
    squads.save_squad(conn, org, game, [p1.player_id])
    squads.publish_squad(conn, org, game)
    seen = squads.get_squad(conn, p2, game)
    assert seen["state"] == "published"
    assert [p["name"] for p in seen["players"]] == ["Игрок Первый"]


def test_player_cannot_form_or_publish_squad(conn, org, p1, mk_event):
    game = mk_event("game")
    with pytest.raises(ServiceError, match="только организатору"):
        squads.save_squad(conn, p1, game, [p1.player_id])
    squads.save_squad(conn, org, game, [p1.player_id])
    with pytest.raises(ServiceError, match="только организатору"):
        squads.publish_squad(conn, p1, game)


def test_squad_exists_only_for_games(conn, org, p1, mk_event):
    training = mk_event("training")
    with pytest.raises(ServiceError, match="только для игр"):
        squads.save_squad(conn, org, training, [p1.player_id])
    with pytest.raises(ServiceError, match="только для игр"):
        squads.get_squad(conn, org, training)


def test_cannot_publish_missing_or_empty_squad(conn, org, p1, mk_event):
    game = mk_event("game")
    with pytest.raises(ServiceError, match="Сначала сохраните состав"):
        squads.publish_squad(conn, org, game)
    squads.save_squad(conn, org, game, [])
    with pytest.raises(ServiceError, match="пустой состав"):
        squads.publish_squad(conn, org, game)


def test_cannot_publish_twice(conn, org, p1, mk_event):
    game = mk_event("game")
    squads.save_squad(conn, org, game, [p1.player_id])
    squads.publish_squad(conn, org, game)
    with pytest.raises(ServiceError, match="уже опубликован"):
        squads.publish_squad(conn, org, game)


def test_changes_to_published_squad_apply_immediately(conn, org, p1, p2, mk_event):
    game = mk_event("game")
    squads.save_squad(conn, org, game, [p1.player_id])
    squads.publish_squad(conn, org, game)
    squads.save_squad(conn, org, game, [p2.player_id])
    seen = squads.get_squad(conn, p1, game)
    assert seen["state"] == "published"
    assert [p["id"] for p in seen["players"]] == [p2.player_id]


def test_published_squad_cannot_become_empty(conn, org, p1, mk_event):
    game = mk_event("game")
    squads.save_squad(conn, org, game, [p1.player_id])
    squads.publish_squad(conn, org, game)
    with pytest.raises(ServiceError, match="не может быть пустым"):
        squads.save_squad(conn, org, game, [])


def test_unknown_player_is_rejected(conn, org, mk_event):
    game = mk_event("game")
    with pytest.raises(ServiceError, match="Игрок не найден"):
        squads.save_squad(conn, org, game, [999])


def test_duplicates_are_ignored(conn, org, p1, mk_event):
    game = mk_event("game")
    squads.save_squad(conn, org, game, [p1.player_id, p1.player_id])
    assert len(squads.get_squad(conn, org, game)["players"]) == 1


def test_cancelled_game_squad_is_locked(conn, org, p1, mk_event, now):
    game = mk_event("game")
    squads.save_squad(conn, org, game, [p1.player_id])
    events.cancel_event(conn, org, game, now=now)
    with pytest.raises(ServiceError, match="отменена"):
        squads.save_squad(conn, org, game, [])
    with pytest.raises(ServiceError, match="отменена"):
        squads.publish_squad(conn, org, game)
