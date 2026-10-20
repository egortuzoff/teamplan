"""Уведомления организатору о наступивших сроках (FR-53 – FR-55).

Фонового процесса нет: проверка выполняется при открытии или обновлении
любой страницы любым пользователем. Дубликаты при повторных и одновременных
проверках исключает уникальный индекс notifications_once в базе.
"""
from datetime import datetime

from .common import DATETIME_FMT, current_time
from .db import transaction
from .events import describe_with_type, is_started
from .notifications import notify_organizers


def _not_answered(conn, event_id):
    """Имена игроков без отметки явки, по алфавиту."""
    rows = conn.execute(
        "SELECT name FROM players WHERE id NOT IN "
        "(SELECT player_id FROM attendance WHERE event_id = %s)",
        (event_id,),
    ).fetchall()
    return sorted((r["name"] for r in rows), key=str.casefold)


def _overdue_replies(conn, now):
    """FR-53: срок «ответить до» прошёл, событие не отменено, есть не ответившие.

    Срок считается прошедшим, когда текущее время позже него, — как подсветка
    не ответивших в сводке явки (FR-22).
    """
    created = 0
    rows = conn.execute(
        "SELECT * FROM events WHERE status = 'planned' AND reply_by IS NOT NULL "
        "AND type <> 'game' AND id NOT IN "
        "(SELECT event_id FROM notifications WHERE kind = 'reply_overdue')"
    ).fetchall()
    for row in rows:
        if current_time(now) <= datetime.strptime(row["reply_by"], DATETIME_FMT):
            continue
        missing = _not_answered(conn, row["id"])
        if not missing:
            continue
        text = (
            f"Срок ответа прошёл: {describe_with_type(row)}. "
            f"Не ответили ({len(missing)}): {', '.join(missing)}."
        )
        created += notify_organizers(conn, row["id"], "reply_overdue", text, now)
    return created


def _games_without_score(conn, now):
    """FR-54: игра началась (с момента начала, как статус FR-48), не отменена, счёта нет."""
    created = 0
    rows = conn.execute(
        "SELECT * FROM events WHERE type = 'game' AND status = 'planned' "
        "AND id NOT IN (SELECT event_id FROM results) AND id NOT IN "
        "(SELECT event_id FROM notifications WHERE kind = 'no_score')"
    ).fetchall()
    for row in rows:
        if not is_started(dict(row), now):
            continue
        text = (
            f"Внесите счёт: {describe_with_type(row)} — игра началась, счёт не внесён. "
            "Откройте карточку игры."
        )
        created += notify_organizers(conn, row["id"], "no_score", text, now)
    return created


def check_deadlines(conn, now=None):
    """Создаёт уведомления FR-53 и FR-54, которые ещё не созданы. Возвращает их число."""
    with transaction(conn, serialized=True):
        return _overdue_replies(conn, now) + _games_without_score(conn, now)
