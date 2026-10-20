"""Явка на тренировки и мероприятия и сводка (FR-16 – FR-22)."""
from datetime import datetime

from . import auth, notifications
from .common import DATETIME_FMT, current_time, stamp
from .db import transaction
from .errors import ServiceError
from .events import _get_event_row, describe_with_type, is_started
from .players import list_players

ATTENDANCE_LABELS = {"yes": "Буду", "maybe": "Под вопросом", "no": "Не буду"}


def set_attendance(conn, actor, event_id, player_id, status, now=None):
    """Отметка явки. Игрок отмечает себя, организатор любого игрока."""
    fresh = auth.require_actor(conn, actor)
    if not fresh.is_organizer and player_id != fresh.player_id:
        raise ServiceError("Игрок может отметить явку только за себя.")
    row = _get_event_row(conn, event_id)
    if row["type"] == "game":
        raise ServiceError("На игры явка не отмечается: состав определяет организатор.")
    if status not in ATTENDANCE_LABELS:
        raise ServiceError("Выберите вариант: «Буду», «Под вопросом» или «Не буду».")
    if row["status"] == "cancelled":
        raise ServiceError("Событие отменено, явку отметить нельзя.")
    event = dict(row)
    if is_started(event, now):
        raise ServiceError("Событие уже началось, отметку изменить нельзя.")
    if conn.execute("SELECT 1 FROM players WHERE id = %s", (player_id,)).fetchone() is None:
        raise ServiceError("Игрок не найден.")
    with transaction(conn):
        previous = conn.execute(
            "SELECT status FROM attendance WHERE event_id = %s AND player_id = %s",
            (event_id, player_id),
        ).fetchone()
        previous = previous["status"] if previous else None
        # Новая отметка заменяет прежнюю (FR-18).
        conn.execute(
            "INSERT INTO attendance (event_id, player_id, status, updated_at) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT(event_id, player_id) DO UPDATE SET "
            "status = excluded.status, updated_at = excluded.updated_at",
            (event_id, player_id, status, stamp(now)),
        )
        # FR-52: игрок сам ответил «Под вопросом» или «Не буду» впервые
        # или изменил ответ. Отметка организатора уведомления не создаёт (FR-35).
        if not fresh.is_organizer and _should_notify(previous, status):
            notifications.notify_organizers(
                conn, event_id, "attendance",
                _attendance_text(conn, player_id, row, previous, status), now,
            )


def _should_notify(previous, status):
    if previous is None:
        return status != "yes"
    return previous != status


def _attendance_text(conn, player_id, row, previous, status):
    name = conn.execute("SELECT name FROM players WHERE id = %s", (player_id,)).fetchone()["name"]
    before = f"«{ATTENDANCE_LABELS[previous]}»" if previous else "ответа не было"
    return (
        f"{name}: явка на {describe_with_type(row)} — «{ATTENDANCE_LABELS[status]}» "
        f"(прежний ответ: {before})."
    )


def attendance_summary(conn, actor, event_id, now=None):
    """Сводка явки по событию (FR-21, FR-22). Доступна организатору и игрокам."""
    auth.require_actor(conn, actor)
    row = _get_event_row(conn, event_id)
    if row["type"] == "game":
        raise ServiceError("Для игры сводка явки не строится.")
    marks = {
        r["player_id"]: r["status"]
        for r in conn.execute(
            "SELECT player_id, status FROM attendance WHERE event_id = %s", (event_id,)
        )
    }
    by_status = {"yes": [], "maybe": [], "no": [], "none": []}
    for player in list_players(conn, actor):
        by_status[marks.get(player["id"], "none")].append(
            {k: player[k] for k in ("id", "name", "number", "position")}
        )
    overdue = bool(
        row["reply_by"]
        and current_time(now) > datetime.strptime(row["reply_by"], DATETIME_FMT)
    )
    return {
        "event_id": event_id,
        "reply_by": row["reply_by"],
        "overdue": overdue,
        # Не ответившие подсвечиваются после срока «ответить до» (FR-22).
        "highlight_missing": overdue and bool(by_status["none"]),
        "marks": marks,
        "by_status": by_status,
        "counts": {key: len(items) for key, items in by_status.items()},
    }
