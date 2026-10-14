"""Лента уведомлений (FR-34 – FR-40, п. 4.5).

Уведомления создаются внутри транзакции действия, которое их вызвало:
если действие не сохранилось, уведомлений тоже нет.
"""
from . import auth
from .common import format_date, stamp
from .db import transaction
from .errors import ServiceError

FEED_LIMIT = 50

KIND_LABELS = {
    "moved": "Перенос",
    "cancelled": "Отмена",
    "squad_published": "Состав",
    "squad_changed": "Состав",
    "result": "Результат",
    "attendance": "Явка",
    "reply_overdue": "Срок ответа",
    "no_score": "Счёт",
}


def describe_event(row):
    """«Название» ДД.ММ.ГГГГ в ЧЧ:ММ — для текста уведомления."""
    title = row["title"] if "«" in row["title"] else f"«{row['title']}»"
    return f"{title} {format_date(row['date'])} в {row['time']}"


def squad_player_ids(conn, event_id):
    return [
        r["player_id"]
        for r in conn.execute(
            "SELECT player_id FROM squad_players WHERE event_id = ?", (event_id,)
        )
    ]


def event_audience(conn, row):
    """Получатели при переносе и отмене (п. 4.5).

    Для игры — игроки опубликованного состава (черновик игрокам не объявлен),
    для тренировки и мероприятия — игроки, отметившие явку.
    """
    if row["type"] == "game":
        state = conn.execute(
            "SELECT state FROM squads WHERE event_id = ?", (row["id"],)
        ).fetchone()
        if state is None or state["state"] != "published":
            return []
        return squad_player_ids(conn, row["id"])
    return [
        r["player_id"]
        for r in conn.execute(
            "SELECT player_id FROM attendance WHERE event_id = ?", (row["id"],)
        )
    ]


def notify(conn, actor, player_ids, event_id, kind, text, now=None):
    """Создаёт уведомления игрокам (вызывается внутри транзакции).

    text — строка или функция player_id -> строка. Организатор не получает
    уведомлений о собственных действиях (FR-35), даже если он также игрок.
    """
    ts = stamp(now)
    for player_id in dict.fromkeys(player_ids):
        user = conn.execute(
            "SELECT id FROM users WHERE player_id = ?", (player_id,)
        ).fetchone()
        if user is None or user["id"] == actor.user_id:
            continue
        message = text(player_id) if callable(text) else text
        conn.execute(
            "INSERT INTO notifications (user_id, event_id, kind, text, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user["id"], event_id, kind, message, ts),
        )


def organizer_user_ids(conn):
    """Аккаунты организаторов — получатели уведомлений FR-52 – FR-54."""
    return [r["id"] for r in conn.execute("SELECT id FROM users WHERE role = 'organizer'")]


def notify_organizers(conn, event_id, kind, text, now=None):
    """Уведомления организатору (вызывается внутри транзакции).

    Для «срок ответа прошёл» и «внесите счёт» уникальный индекс
    notifications_once не даёт создать второе такое уведомление
    на то же событие тому же получателю (FR-53 – FR-55): повторная
    вставка пропускается. Возвращает число созданных уведомлений.
    """
    created = 0
    for user_id in organizer_user_ids(conn):
        cur = conn.execute(
            "INSERT INTO notifications (user_id, event_id, kind, text, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (kind, event_id, user_id) "
            "WHERE kind IN ('reply_overdue', 'no_score') DO NOTHING",
            (user_id, event_id, kind, text, stamp(now)),
        )
        created += cur.rowcount
    return created


def list_notifications(conn, actor):
    """Последние 50 уведомлений пользователя, новые сверху (FR-36, FR-37)."""
    fresh = auth.require_actor(conn, actor)
    rows = conn.execute(
        "SELECT id, event_id, kind, text, created_at, read_at FROM notifications "
        "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (fresh.user_id, FEED_LIMIT),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["kind_label"] = KIND_LABELS[item["kind"]]
        item["is_read"] = item["read_at"] is not None
        result.append(item)
    return result


def unread_count(conn, actor):
    """Число непрочитанных уведомлений пользователя (FR-39)."""
    fresh = auth.require_actor(conn, actor)
    return conn.execute(
        "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read_at IS NULL",
        (fresh.user_id,),
    ).fetchone()[0]


def mark_read(conn, actor, notification_id, now=None):
    """Открытие уведомления делает его прочитанным (FR-40). Только своё (FR-36)."""
    fresh = auth.require_actor(conn, actor)
    row = conn.execute(
        "SELECT id FROM notifications WHERE id = ? AND user_id = ?",
        (notification_id, fresh.user_id),
    ).fetchone()
    if row is None:
        raise ServiceError("Уведомление не найдено.")
    with transaction(conn):
        conn.execute(
            "UPDATE notifications SET read_at = ? WHERE id = ? AND read_at IS NULL",
            (stamp(now), notification_id),
        )


def mark_all_read(conn, actor, now=None):
    """Отмечает все свои уведомления прочитанными одним действием (FR-40)."""
    fresh = auth.require_actor(conn, actor)
    with transaction(conn):
        conn.execute(
            "UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL",
            (stamp(now), fresh.user_id),
        )
