"""События: тренировки, игры и мероприятия (FR-9 – FR-15, FR-41, FR-42, FR-48)."""
from datetime import datetime

from . import auth, notifications
from .common import (
    DATETIME_FMT, clean_text, current_time, event_start, format_date, norm_date,
    norm_optional_datetime, norm_time, stamp,
)
from .db import transaction
from .errors import ServiceError

EVENT_TYPES = {"training": "Тренировка", "game": "Игра", "activity": "Мероприятие"}
STATUS_LABELS = {"planned": "Запланировано", "cancelled": "Отменено"}
# Статус для показа (FR-48): в базе хранится только признак отмены, остальное вычисляется.
DISPLAY_STATUS_LABELS = {
    "cancelled": "Отменено",
    "planned": "Запланировано",
    "past": "Прошло",
    "played": "Сыграна",
    "no_score": "Счёт не внесён",
}


def _get_event_row(conn, event_id):
    row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        raise ServiceError("Событие не найдено.")
    return row


def is_started(event, now=None):
    """Событие уже началось (event — словарь из get_event или list_events)."""
    return event_start(event["date"], event["time"]) <= current_time(now)


def display_status(event, has_score, now=None):
    """Статус события для показа (FR-48).

    Отменённое — «Отменено»; до начала — «Запланировано»; с момента начала
    тренировка и мероприятие — «Прошло», игра — «Сыграна», если счёт внесён,
    иначе «Счёт не внесён». Время — местное (часовой пояс TZ).
    """
    if event["status"] == "cancelled":
        return "cancelled"
    if not is_started(event, now):
        return "planned"
    if event["type"] != "game":
        return "past"
    return "played" if has_score else "no_score"


def _scored_event_ids(conn):
    return {r["event_id"] for r in conn.execute("SELECT event_id FROM results")}


def _to_dict(row, scored, now=None):
    item = dict(row)
    item["type_label"] = EVENT_TYPES[item["type"]]
    item["status_label"] = STATUS_LABELS[item["status"]]
    item["start"] = event_start(item["date"], item["time"])
    item["display_status"] = display_status(item, item["id"] in scored, now)
    item["display_status_label"] = DISPLAY_STATUS_LABELS[item["display_status"]]
    return item


def _check_reply_by(event_type, reply_by, date, time, now=None, previous=None):
    """Срок «ответить до» (FR-42).

    Новый или изменённый срок не может быть в прошлом. Если срок не менялся
    (previous), правка других полей после его наступления не блокируется.
    Срок в любом случае не может быть позже начала события.
    """
    reply_by = norm_optional_datetime(reply_by, "Ответить до")
    if reply_by is None:
        return None
    if event_type == "game":
        raise ServiceError("Для игры срок ответа не задаётся: на игры явка не отмечается.")
    moment = datetime.strptime(reply_by, DATETIME_FMT)
    if reply_by != previous and moment < current_time(now).replace(second=0, microsecond=0):
        raise ServiceError("Срок «ответить до» не может быть в прошлом.")
    if moment > event_start(date, time):
        raise ServiceError("Срок «ответить до» не может быть позже начала события.")
    return reply_by


def _check_gather_time(gather_time, start_time):
    """Время сбора (FR-41): для событий любого типа, не позже начала."""
    if gather_time is None or gather_time == "":
        return None
    gather_time = norm_time(gather_time, "Время сбора")
    if gather_time > start_time:
        raise ServiceError("Время сбора не может быть позже времени начала события.")
    return gather_time


def create_event(conn, actor, event_type, title, date, time, place=None,
                 reply_by=None, now=None, gather_time=None):
    """Организатор создаёт событие (FR-9, FR-10). Возвращает id события."""
    auth.require_organizer(conn, actor)
    if event_type not in EVENT_TYPES:
        raise ServiceError("Тип события: выберите тренировку, игру или мероприятие.")
    title = clean_text(title, "Название", 120)
    date = norm_date(date, "Дата")
    time = norm_time(time, "Время")
    place = clean_text(place, "Место", 120, required=False)
    if date < current_time(now).strftime("%Y-%m-%d"):
        raise ServiceError("Дата события не может быть в прошлом.")
    gather_time = _check_gather_time(gather_time, time)
    reply_by = _check_reply_by(event_type, reply_by, date, time, now)
    with transaction(conn):
        ts = stamp(now)
        return conn.execute(
            "INSERT INTO events (type, title, date, time, gather_time, place, status, "
            "reply_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'planned', ?, ?, ?)",
            (event_type, title, date, time, gather_time, place, reply_by, ts, ts),
        ).lastrowid


def _move_text(row, date, time, gather_time, place):
    """Текст уведомления о переносе: что было и что стало."""
    changes = []
    if date != row["date"]:
        changes.append(f"дата {format_date(row['date'])} → {format_date(date)}")
    if time != row["time"]:
        changes.append(f"время {row['time']} → {time}")
    if gather_time != row["gather_time"]:
        changes.append(
            f"время сбора {row['gather_time'] or 'не указано'} → {gather_time or 'не указано'}"
        )
    if place != row["place"]:
        changes.append(f"место {row['place'] or 'не указано'} → {place or 'не указано'}")
    return f"Перенос: {notifications.describe_event(row)}. Изменено: " + "; ".join(changes) + "."


def update_event(conn, actor, event_id, title, date, time, place=None,
                 reply_by=None, now=None, gather_time=None):
    """Организатор меняет поля события (FR-11). Тип события не меняется.

    Отметки явки при этом сохраняются. При изменении даты, времени, времени
    сбора или места игроки получают уведомление о переносе (п. 4.5).
    """
    auth.require_organizer(conn, actor)
    row = _get_event_row(conn, event_id)
    if row["status"] == "cancelled":
        raise ServiceError("Отменённое событие изменить нельзя.")
    title = clean_text(title, "Название", 120)
    date = norm_date(date, "Дата")
    time = norm_time(time, "Время")
    place = clean_text(place, "Место", 120, required=False)
    if date != row["date"] and date < current_time(now).strftime("%Y-%m-%d"):
        raise ServiceError("Дата события не может быть в прошлом.")
    gather_time = _check_gather_time(gather_time, time)
    reply_by = _check_reply_by(row["type"], reply_by, date, time, now, previous=row["reply_by"])
    moved = (date, time, gather_time, place) != (
        row["date"], row["time"], row["gather_time"], row["place"]
    )
    with transaction(conn):
        conn.execute(
            "UPDATE events SET title = ?, date = ?, time = ?, gather_time = ?, "
            "place = ?, reply_by = ?, updated_at = ? WHERE id = ?",
            (title, date, time, gather_time, place, reply_by, stamp(now), event_id),
        )
        if moved:
            notifications.notify(
                conn, actor, notifications.event_audience(conn, row), event_id, "moved",
                _move_text(row, date, time, gather_time, place), now,
            )


def cancel_event(conn, actor, event_id, now=None):
    """Отмена: событие остаётся в списке со статусом «Отменено» (FR-12).

    Игроки получают уведомление об отмене (п. 4.5).
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
        notifications.notify(
            conn, actor, notifications.event_audience(conn, row), event_id, "cancelled",
            f"Отменено: {notifications.describe_event(row)}.", now,
        )


def has_result(conn, event_id):
    """True, если у игры внесён счёт или голы (FR-14)."""
    return conn.execute(
        "SELECT 1 FROM results WHERE event_id = ?", (event_id,)
    ).fetchone() is not None


def delete_event(conn, actor, event_id):
    """Удаление события без результата (FR-13, FR-14).

    Отметки явки, состав и связанные уведомления удаляются вместе с событием,
    новые уведомления не создаются.
    """
    auth.require_organizer(conn, actor)
    _get_event_row(conn, event_id)
    if has_result(conn, event_id):
        raise ServiceError(
            "Игру с внесённым результатом удалить нельзя. "
            "Ошибки исправляются редактированием результата."
        )
    with transaction(conn):
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))


def list_events(conn, actor, now=None):
    """Все события по дате и времени со статусом для показа (FR-15, FR-48)."""
    auth.require_actor(conn, actor)
    rows = conn.execute(
        "SELECT * FROM events ORDER BY date, time, id"
    ).fetchall()
    scored = _scored_event_ids(conn)
    return [_to_dict(row, scored, now) for row in rows]


def get_event(conn, actor, event_id, now=None):
    auth.require_actor(conn, actor)
    return _to_dict(_get_event_row(conn, event_id), _scored_event_ids(conn), now)
