"""Список игроков и их аккаунты (FR-4 – FR-8)."""
from . import auth
from .common import clean_text, parse_number, stamp
from .db import transaction
from .errors import ServiceError


def _clean_player_fields(name, number, position):
    return (
        clean_text(name, "Имя", 100),
        parse_number(number),
        clean_text(position, "Позиция", 50, required=False),
    )


def _get_player(conn, player_id):
    row = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
    if row is None:
        raise ServiceError("Игрок не найден.")
    return row


def list_players(conn, actor):
    """Все игроки по алфавиту. Логины видит только организатор."""
    fresh = auth.require_actor(conn, actor)
    rows = conn.execute(
        "SELECT p.id, p.name, p.number, p.position, u.login "
        "FROM players p LEFT JOIN users u ON u.player_id = p.id"
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        if not fresh.is_organizer:
            item["login"] = None
        result.append(item)
    result.sort(key=lambda p: (p["name"].casefold(), p["id"]))
    return result


def create_player_account(conn, actor, name, login, password,
                          number=None, position=None, now=None):
    """Организатор создаёт игрока и его аккаунт (FR-4, FR-7). Возвращает id игрока."""
    auth.require_organizer(conn, actor)
    name, number, position = _clean_player_fields(name, number, position)
    login = auth.clean_login(login)
    password_hash = auth.hash_password(password)
    with transaction(conn):
        if auth.login_exists(conn, login):
            raise ServiceError("Такой логин уже занят.")
        ts = stamp(now)
        player_id = conn.execute(
            "INSERT INTO players (name, number, position, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, number, position, ts, ts),
        ).lastrowid
        auth.insert_user(conn, login, password_hash, auth.ROLE_PLAYER, player_id, now)
    return player_id


def update_player(conn, actor, player_id, name, number=None, position=None, now=None):
    """Организатор меняет данные игрока (FR-7)."""
    auth.require_organizer(conn, actor)
    _get_player(conn, player_id)
    name, number, position = _clean_player_fields(name, number, position)
    with transaction(conn):
        conn.execute(
            "UPDATE players SET name = ?, number = ?, position = ?, updated_at = ? "
            "WHERE id = ?",
            (name, number, position, stamp(now), player_id),
        )


def set_player_password(conn, actor, player_id, new_password, now=None):
    """Организатор задаёт игроку новый пароль (FR-5). Игрок сам пароль не меняет."""
    auth.require_organizer(conn, actor)
    _get_player(conn, player_id)
    row = conn.execute(
        "SELECT id FROM users WHERE player_id = ?", (player_id,)
    ).fetchone()
    if row is None:
        raise ServiceError("У игрока нет аккаунта.")
    auth.set_password(conn, row["id"], new_password, now)


def delete_player(conn, actor, player_id):
    """Удаляет игрока и его аккаунт (FR-8).

    Нельзя удалить игрока, включённого в состав.
    TODO этап 2: так же запретить удаление, если у игрока есть голы или передачи.
    """
    auth.require_organizer(conn, actor)
    _get_player(conn, player_id)
    in_squad = conn.execute(
        "SELECT 1 FROM squad_players WHERE player_id = ?", (player_id,)
    ).fetchone()
    if in_squad:
        raise ServiceError(
            "Игрока нельзя удалить: он включён в состав на игру. "
            "Сначала уберите его из состава."
        )
    with transaction(conn):
        conn.execute(
            "DELETE FROM users WHERE player_id = ? AND role = ?",
            (player_id, auth.ROLE_PLAYER),
        )
        conn.execute("DELETE FROM players WHERE id = ?", (player_id,))
