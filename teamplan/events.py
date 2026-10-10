"""События: тренировки, сборы и игры (FR-9 – FR-15)."""
from . import auth
from .common import (
    clean_text, current_time, event_start, norm_date, norm_optional_datetime,
    norm_time, stamp,
)
from .db import transaction
from .errors import ServiceError

EVENT_TYPES = {"training": "Тренировка", "camp": "Сбор", "game": "Игра"}
STATUS_LABELS = {"planned": "Запланировано", "cancelled": "Отменено"}


def _get_event_row(conn, event_id):
    row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        raise ServiceError("Событие не найдено.")
    return row


def _to_dict(row):
    item = dict(row)
    item["type_label"] = EVENT_TYPES[item["type"]]
    item["status_label"] = STATUS_LABELS[item["status"]]
    item["start"] = event_start(item["date"], item["time"])
    return item


def is_started(event, now=None):
    """Событие уже началось (event — словарь из get_event или list_events)."""
    return event_start(event["date"], event["time"]) <= current_time(now)


def _check_reply_by(event_type, reply_by):
    reply_by = norm_optional_datetime(reply_by, "Ответить до")
    if reply_by and event_type == "game":
        raise ServiceError("Для игры срок ответа не задаётся: на игры явка не отмечается.")
    return reply_by


def create_event(conn, actor, event_type, title, date, time, place=None,
                 reply_by=None, now=None):
    """Организатор создаёт событие (FR-9, FR-10). Возвращает id события."""
    auth.require_organizer(conn, actor)
    if event_type not in EVENT_TYPES:
        raise ServiceError("Тип события: выберите тренировку, сбор или игру.")
    title = clean_text(title, "Название", 120)
    date = norm_date(date, "Дата")
    time = norm_time(time, "Время")
    place = clean_text(place, "Место", 120, required=False)
    reply_by = _check_reply_by(event_type, reply_by)
    if date < current_time(now).strftime("%Y-%m-%d"):
        raise ServiceError("Дата события не может быть в прошлом.")
    with transaction(conn):
        ts = stamp(now)
        return conn.execute(
            "INSERT INTO events (type, title, date, time, place, status, reply_by, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'planned', ?, ?, ?)",
            (event_type, title, date, time, place, reply_by, ts, ts),
        ).lastrowid


def update_event(conn, actor, event_id, title, date, time, place=None,
                 reply_by=None, now=None):
    """Организатор меняет поля события (FR-11). Тип события не меняется.

    Отметки явки при этом сохраняются.
    TODO этап 2: создавать уведомление при изменении даты, времени или места.
    """
    auth.require_organizer(conn, actor)
    row = _get_event_row(conn, event_id)
    if row["status"] == "cancelled":
        raise ServiceError("Отменённое событие изменить нельзя.")
    title = clean_text(title, "Название", 120)
    date = norm_date(date, "Дата")
    time = norm_time(time, "Время")
    place = clean_text(place, "Место", 120, required=False)
    reply_by = _check_reply_by(row["type"], reply_by)
    if date != row["date"] and date < current_time(now).strftime("%Y-%m-%d"):
        raise ServiceError("Дата события не может быть в прошлом.")
    with transaction(conn):
        conn.execute(
            "UPDATE events SET title = ?, date = ?, time = ?, place = ?, "
            "reply_by = ?, updated_at = ? WHERE id = ?",
            (title, date, time, place, reply_by, stamp(now), event_id),
        )


def cancel_event(conn, actor, event_id, now=None):
    """Отмена: событие остаётся в списке со статусом «Отменено» (FR-12).

    TODO этап 2: создавать уведомление игрокам об отмене.
    """
    auth.require_organizer(conn, actor)
    row = _get_event_row(conn, event_id)
    if row["status"] == "cancelled":
        raise ServiceError("Событие уже отменено.")
    if event_start(row["date"], row["time"]) <= current_time(now):
        raise ServiceError("Событие уже началось, отменить его нельзя.")
    with transaction(conn):
        conn.execute(
            "UPDATE events SET status = 'cancelled', updated_at = ? WHERE id = ?",
            (stamp(now), event_id),
        )


def _has_result(conn, event_id):
    """TODO этап 2: True, если у игры есть счёт или голы (FR-14)."""
    return False


def delete_event(conn, actor, event_id):
    """Удаление события без результата (FR-13).

    Отметки явки и состав удаляются вместе с событием, уведомления не создаются.
    """
    auth.require_organizer(conn, actor)
    _get_event_row(conn, event_id)
    if _has_result(conn, event_id):
        raise ServiceError(
            "Игру с внесённым результатом удалить нельзя. "
            "Ошибки исправляются редактированием."
        )
    with transaction(conn):
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))


def list_events(conn, actor):
    """Все события по дате и времени (FR-15)."""
    auth.require_actor(conn, actor)
    rows = conn.execute(
        "SELECT * FROM events ORDER BY date, time, id"
    ).fetchall()
    return [_to_dict(row) for row in rows]


def get_event(conn, actor, event_id):
    auth.require_actor(conn, actor)
    return _to_dict(_get_event_row(conn, event_id))
