"""Сводные таблицы расписания по типам событий (FR-43 – FR-45)."""
from . import auth, events
from .errors import ServiceError

NO_VALUE = "—"
OUTCOME_WIN = "Победа"
OUTCOME_DRAW = "Ничья"
OUTCOME_LOSS = "Поражение"


def game_outcome(our_score, their_score):
    """Итог игры по счёту (FR-44); None, если счёт не внесён."""
    if our_score is None or their_score is None:
        return None
    if our_score > their_score:
        return OUTCOME_WIN
    if our_score == their_score:
        return OUTCOME_DRAW
    return OUTCOME_LOSS


def score_text(our_score, their_score):
    """Счёт «наша команда : соперник» или прочерк, если счёт не внесён."""
    if our_score is None or their_score is None:
        return NO_VALUE
    return f"{our_score} : {their_score}"


def filter_by_type(evs, event_type):
    """События одного типа в исходном порядке; None — все события (вкладка «Все»)."""
    if event_type is None:
        return list(evs)
    if event_type not in events.EVENT_TYPES:
        raise ServiceError("Тип события: выберите тренировку, игру или мероприятие.")
    return [e for e in evs if e["type"] == event_type]


def games_table(conn, actor):
    """Игры по дате и времени со счётом и итогом (FR-43, FR-44).

    К каждому событию добавляются our_score, their_score (None без результата),
    score — текст счёта и outcome — итог или прочерк.
    """
    evs = filter_by_type(events.list_events(conn, actor), "game")
    scores = {
        r["event_id"]: (r["our_score"], r["their_score"])
        for r in conn.execute("SELECT event_id, our_score, their_score FROM results")
    }
    rows = []
    for ev in evs:
        our, their = scores.get(ev["id"], (None, None))
        rows.append({
            **ev, "our_score": our, "their_score": their,
            "score": score_text(our, their),
            "outcome": game_outcome(our, their) or NO_VALUE,
        })
    return rows


def attendance_counts(marks, player_ids):
    """Число ответов «буду», «под вопросом», «не буду» и не ответивших (FR-45).

    marks — словарь {игрок: отметка}; учитываются только игроки из player_ids,
    как в сводке по событию (FR-21).
    """
    counts = {"yes": 0, "maybe": 0, "no": 0, "none": 0}
    for pid in player_ids:
        counts[marks.get(pid, "none")] += 1
    return counts


def attendance_table(conn, actor, event_type):
    """Тренировки или мероприятия по дате и времени со сводкой явки (FR-43, FR-45).

    К каждому событию добавляется counts — словарь с ключами yes, maybe, no, none.
    """
    auth.require_actor(conn, actor)
    if event_type == "game":
        raise ServiceError("Для игры сводка явки не строится.")
    evs = filter_by_type(events.list_events(conn, actor), event_type)
    player_ids = [r["id"] for r in conn.execute("SELECT id FROM players")]
    marks = {}
    for r in conn.execute("SELECT event_id, player_id, status FROM attendance"):
        marks.setdefault(r["event_id"], {})[r["player_id"]] = r["status"]
    return [
        {**ev, "counts": attendance_counts(marks.get(ev["id"], {}), player_ids)}
        for ev in evs
    ]
