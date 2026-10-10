"""Состав на игру: черновик и публикация (FR-23 – FR-26)."""
from . import auth, notifications
from .common import stamp
from .db import transaction
from .errors import ServiceError
from .events import _get_event_row

STATE_LABELS = {
    "none": "состав ещё не сформирован",
    "draft": "черновик (игроки его не видят)",
    "published": "опубликован",
}


def _get_game(conn, event_id):
    row = _get_event_row(conn, event_id)
    if row["type"] != "game":
        raise ServiceError("Состав формируется только для игр.")
    return row


def _squad_players(conn, event_id):
    rows = conn.execute(
        "SELECT p.id, p.name, p.number, p.position "
        "FROM squad_players sp JOIN players p ON p.id = sp.player_id "
        "WHERE sp.event_id = ?",
        (event_id,),
    ).fetchall()
    result = [dict(r) for r in rows]
    result.sort(key=lambda p: (p["name"].casefold(), p["id"]))
    return result


def _squad_state(conn, event_id):
    row = conn.execute("SELECT state FROM squads WHERE event_id = ?", (event_id,)).fetchone()
    return row["state"] if row else "none"


def get_squad(conn, actor, event_id):
    """Состав на игру.

    Организатор видит черновик и опубликованный состав. Игрок видит только
    опубликованный; иначе получает состояние «none» и пустой список (FR-25).
    """
    fresh = auth.require_actor(conn, actor)
    _get_game(conn, event_id)
    state = _squad_state(conn, event_id)
    if fresh.is_organizer or state == "published":
        return {"event_id": event_id, "state": state,
                "players": _squad_players(conn, event_id)}
    return {"event_id": event_id, "state": "none", "players": []}


def _normalize_ids(conn, player_ids):
    ids = []
    for value in player_ids or []:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ServiceError("Игрок не найден.")
        if value not in ids:
            ids.append(value)
    for player_id in ids:
        if conn.execute("SELECT 1 FROM players WHERE id = ?", (player_id,)).fetchone() is None:
            raise ServiceError("Игрок не найден.")
    return ids


def _check_scorers_stay(conn, event_id, ids):
    """Игрока с голом или передачей в этой игре нельзя убрать из состава (FR-29)."""
    rows = conn.execute(
        "SELECT DISTINCT p.id, p.name FROM goals g JOIN players p "
        "ON p.id = g.scorer_id OR p.id = g.assist_id WHERE g.event_id = ?",
        (event_id,),
    ).fetchall()
    removed = sorted(r["name"] for r in rows if r["id"] not in ids)
    if removed:
        raise ServiceError(
            "Нельзя убрать из состава игроков с голом или передачей в этой игре: "
            + ", ".join(removed) + ". Сначала исправьте результат."
        )


def _notify_squad_changed(conn, actor, row, old_ids, new_ids, now):
    """Уведомление игрокам прежнего и нового состава (FR-38)."""
    game = notifications.describe_event(row)

    def text(player_id):
        if player_id not in old_ids:
            return f"Изменён состав на игру {game}: вы включены в состав."
        if player_id not in new_ids:
            return f"Изменён состав на игру {game}: вы больше не в составе."
        return f"Изменён состав на игру {game}. Вы по-прежнему в составе."

    notifications.notify(
        conn, actor, list(old_ids) + list(new_ids), row["id"], "squad_changed", text, now
    )


def save_squad(conn, actor, event_id, player_ids, now=None):
    """Сохраняет состав из списка всех игроков (FR-23).

    Новый состав сохраняется как черновик. Если состав уже опубликован,
    изменение вступает в силу сразу (FR-26), а уведомление получают игроки
    прежнего и нового состава (FR-38).
    """
    auth.require_organizer(conn, actor)
    row = _get_game(conn, event_id)
    if row["status"] == "cancelled":
        raise ServiceError("Игра отменена, состав изменить нельзя.")
    ids = _normalize_ids(conn, player_ids)
    state = _squad_state(conn, event_id)
    if state == "published" and not ids:
        raise ServiceError("Опубликованный состав не может быть пустым.")
    old_ids = notifications.squad_player_ids(conn, event_id)
    _check_scorers_stay(conn, event_id, ids)
    with transaction(conn):
        ts = stamp(now)
        if state == "none":
            conn.execute(
                "INSERT INTO squads (event_id, state, created_at, updated_at) "
                "VALUES (?, 'draft', ?, ?)",
                (event_id, ts, ts),
            )
        else:
            conn.execute(
                "UPDATE squads SET updated_at = ? WHERE event_id = ?", (ts, event_id)
            )
        conn.execute("DELETE FROM squad_players WHERE event_id = ?", (event_id,))
        conn.executemany(
            "INSERT INTO squad_players (event_id, player_id) VALUES (?, ?)",
            [(event_id, player_id) for player_id in ids],
        )
        if state == "published" and set(ids) != set(old_ids):
            _notify_squad_changed(conn, actor, row, old_ids, ids, now)


def publish_squad(conn, actor, event_id, now=None):
    """Публикует состав: после этого его видят игроки (FR-24).

    Игроки из состава получают уведомление (п. 4.5).
    """
    auth.require_organizer(conn, actor)
    row = _get_game(conn, event_id)
    if row["status"] == "cancelled":
        raise ServiceError("Игра отменена, состав опубликовать нельзя.")
    state = _squad_state(conn, event_id)
    if state == "none":
        raise ServiceError("Сначала сохраните состав.")
    if state == "published":
        raise ServiceError("Состав уже опубликован.")
    if not _squad_players(conn, event_id):
        raise ServiceError("Нельзя опубликовать пустой состав: выберите хотя бы одного игрока.")
    with transaction(conn):
        conn.execute(
            "UPDATE squads SET state = 'published', updated_at = ? WHERE event_id = ?",
            (stamp(now), event_id),
        )
        notifications.notify(
            conn, actor, notifications.squad_player_ids(conn, event_id), event_id,
            "squad_published",
            f"Опубликован состав на игру {notifications.describe_event(row)}. Вы в составе.",
            now,
        )
