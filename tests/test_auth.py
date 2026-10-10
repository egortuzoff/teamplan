import pytest

from teamplan import auth, players
from teamplan.errors import ServiceError


def test_login_returns_role_and_player(conn, org, p1):
    assert org.role == "organizer"
    assert p1.role == "player"
    assert p1.player_id is not None
    assert p1.name == "Игрок Первый"


def test_wrong_password_and_unknown_login_give_same_message(conn, org):
    with pytest.raises(ServiceError) as wrong_password:
        auth.login(conn, "trener", "неверный")
    with pytest.raises(ServiceError) as unknown_login:
        auth.login(conn, "нет-такого", "pass-org")
    assert str(wrong_password.value) == str(unknown_login.value)
    assert "Неверный логин или пароль" in str(wrong_password.value)


def test_empty_fields_rejected(conn, org):
    with pytest.raises(ServiceError, match="Введите логин и пароль"):
        auth.login(conn, "", "")


def test_password_is_not_stored_in_plain_text(conn, org):
    stored = conn.execute("SELECT password_hash FROM users WHERE login = 'trener'").fetchone()[0]
    assert "pass-org" not in stored
    assert stored.startswith("$2")


def test_player_cannot_change_any_password(conn, org, p1, p2):
    with pytest.raises(ServiceError, match="только организатору"):
        players.set_player_password(conn, p1, p1.player_id, "новый-пароль")
    with pytest.raises(ServiceError, match="только организатору"):
        players.set_player_password(conn, p1, p2.player_id, "новый-пароль")


def test_organizer_sets_new_password(conn, org, p1):
    players.set_player_password(conn, org, p1.player_id, "new-pass-1")
    assert auth.login(conn, "igrok1", "new-pass-1").player_id == p1.player_id
    with pytest.raises(ServiceError):
        auth.login(conn, "igrok1", "pass-igrok1")


def test_too_long_password_rejected(conn, org):
    with pytest.raises(ServiceError, match="слишком длинный"):
        players.create_player_account(conn, org, "Игрок", "long", "я" * 40)


def test_missing_user_loses_access(conn, org, p1):
    players.delete_player(conn, org, p1.player_id)
    with pytest.raises(ServiceError, match="Сессия недействительна"):
        players.list_players(conn, p1)
