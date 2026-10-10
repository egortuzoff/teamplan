import pytest

from teamplan import auth, players
from teamplan.errors import ServiceError


def test_login_must_be_unique_ignoring_case(conn, org, p1):
    with pytest.raises(ServiceError, match="занят"):
        players.create_player_account(conn, org, "Другой", "IGROK1", "pass-x")
    assert len(players.list_players(conn, org)) == 1


def test_player_cannot_manage_players(conn, org, p1):
    with pytest.raises(ServiceError, match="только организатору"):
        players.create_player_account(conn, p1, "Новый", "new", "pass-new")
    with pytest.raises(ServiceError, match="только организатору"):
        players.update_player(conn, p1, p1.player_id, "Другое имя")
    with pytest.raises(ServiceError, match="только организатору"):
        players.delete_player(conn, p1, p1.player_id)


def test_name_and_number_are_validated(conn, org):
    with pytest.raises(ServiceError, match="Имя"):
        players.create_player_account(conn, org, "  ", "x1", "pass-x")
    with pytest.raises(ServiceError, match="Игровой номер"):
        players.create_player_account(conn, org, "Игрок", "x2", "pass-x", number="abc")
    with pytest.raises(ServiceError, match="Игровой номер"):
        players.create_player_account(conn, org, "Игрок", "x3", "pass-x", number=1000)


def test_optional_fields_can_be_empty(conn, org):
    player_id = players.create_player_account(conn, org, "Игрок", "x4", "pass-x", "", "")
    row = [p for p in players.list_players(conn, org) if p["id"] == player_id][0]
    assert row["number"] is None and row["position"] is None


def test_update_player(conn, org, p1):
    players.update_player(conn, org, p1.player_id, "Новое Имя", "11", "защитник")
    row = [p for p in players.list_players(conn, org) if p["id"] == p1.player_id][0]
    assert (row["name"], row["number"], row["position"]) == ("Новое Имя", 11, "защитник")


def test_delete_player_removes_account(conn, org, p1):
    players.delete_player(conn, org, p1.player_id)
    with pytest.raises(ServiceError):
        auth.login(conn, "igrok1", "pass-igrok1")
    assert players.list_players(conn, org) == []


def test_cannot_delete_player_in_squad(conn, org, p1, mk_event):
    from teamplan import squads

    game = mk_event("game")
    squads.save_squad(conn, org, game, [p1.player_id])
    with pytest.raises(ServiceError, match="включён в состав"):
        players.delete_player(conn, org, p1.player_id)
    assert len(players.list_players(conn, org)) == 1


def test_players_do_not_see_logins(conn, org, p1, p2):
    assert all(p["login"] for p in players.list_players(conn, org))
    assert all(p["login"] is None for p in players.list_players(conn, p1))


def test_organizer_can_also_be_a_player(conn):
    auth.create_organizer(conn, "boss", "pass-boss", player_name="Тренер-игрок")
    boss = auth.login(conn, "boss", "pass-boss")
    assert boss.is_organizer and boss.player_id is not None
    players.delete_player(conn, boss, boss.player_id)
    assert auth.login(conn, "boss", "pass-boss").is_organizer


def test_cannot_delete_player_with_goal_or_assist(conn, org, p1, p2, played_game, now):
    from teamplan import results, squads

    results.save_result(conn, org, played_game, 1, 0, [(p1.player_id, p2.player_id)], now=now)
    # Голы проверяются раньше состава: причина отказа — именно гол или передача.
    for player in (p1, p2):
        with pytest.raises(ServiceError, match="гол или передача"):
            players.delete_player(conn, org, player.player_id)
    assert len(players.list_players(conn, org)) == 2
    assert squads.get_squad(conn, org, played_game)["state"] == "published"
