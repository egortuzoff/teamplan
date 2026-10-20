"""Главный экран: подборки данных для блоков игрока и организатора (FR-49 – FR-51).

Здесь только чтение. Действия с главного экрана (явка, публикация состава)
выполняются теми же функциями, что и в карточке события, с той же проверкой прав.
"""
from datetime import timedelta

from . import attendance, auth, events, results, squads, summary
from .common import current_time

WEEK_DAYS = 7


def plural(n, forms):
    """Форма слова для числа: plural(3, ("день", "дня", "дней")) -> "дня"."""
    n = abs(n) % 100
    if 11 <= n <= 14:
        return forms[2]
    if n % 10 == 1:
        return forms[0]
    if 2 <= n % 10 <= 4:
        return forms[1]
    return forms[2]


def days_until(event, now=None):
    """Через сколько календарных дней событие (0 — сегодня)."""
    return (event["start"].date() - current_time(now).date()).days


def days_until_text(days):
    if days <= 0:
        return "сегодня"
    if days == 1:
        return "завтра"
    return f"через {days} {plural(days, ('день', 'дня', 'дней'))}"


def _upcoming(evs, now):
    """Будущие события (ещё не начались), включая отменённые, по дате."""
    return [e for e in evs if e["start"] >= now]


def _in_week(evs, now):
    """События ближайших 7 дней, которые ещё не начались."""
    end = now + timedelta(days=WEEK_DAYS)
    return [e for e in _upcoming(evs, now) if e["start"] < end]


def _squad_info(conn, actor, event):
    """Состав игры глазами пользователя: игрок видит только опубликованный (FR-25)."""
    squad = squads.get_squad(conn, actor, event["id"])
    published = squad["state"] == "published"
    ids = {p["id"] for p in squad["players"]}
    return {
        "squad_state": squad["state"],
        "squad_players": squad["players"],
        "squad_published": published,
        "in_squad": published and actor.player_id is not None and actor.player_id in ids,
    }


def _my_marks(conn, player_id):
    if player_id is None:
        return {}
    return {
        r["event_id"]: r["status"]
        for r in conn.execute(
            "SELECT event_id, status FROM attendance WHERE player_id = %s", (player_id,)
        )
    }


def _last_match(conn, actor, evs):
    """Последняя сыгранная игра (FR-48 «Сыграна») со счётом и голами или None."""
    played = [e for e in evs if e["display_status"] == "played"]
    if not played:
        return None
    game = played[-1]
    result = results.get_result(conn, actor, game["id"])
    return {
        **game,
        "our_score": result["our_score"],
        "their_score": result["their_score"],
        "outcome": summary.game_outcome(result["our_score"], result["their_score"]),
        "goals": result["goals"],
    }


def _my_stats(conn, actor):
    """Игры, голы, передачи игрока (FR-32, FR-33); None, если пользователь не игрок."""
    if actor.player_id is None:
        return None
    for row in results.player_stats(conn, actor):
        if row["id"] == actor.player_id:
            return {key: row[key] for key in ("games", "goals", "assists")}
    return {"games": 0, "goals": 0, "assists": 0}


def player_home(conn, actor, now=None):
    """Данные главного экрана игрока (FR-49).

    next_event — ближайшее неотменённое событие или None; для игры добавлены
    сведения о составе (in_squad — игрок в опубликованном составе).
    last_match — последняя сыгранная игра или None.
    week — события ближайших 7 дней; у тренировок и мероприятий my_mark
    (отметка игрока или None) и can_mark — можно ли отметиться (FR-16 – FR-19).
    stats — игры, голы, передачи или None.
    """
    fresh = auth.require_actor(conn, actor)
    now = current_time(now)
    evs = events.list_events(conn, fresh, now)
    upcoming = [e for e in _upcoming(evs, now) if e["status"] != "cancelled"]

    next_event = None
    if upcoming:
        ev = upcoming[0]
        next_event = {**ev, "days_until": days_until(ev, now)}
        if ev["type"] == "game":
            next_event.update(_squad_info(conn, fresh, ev))

    marks = _my_marks(conn, fresh.player_id)
    week = []
    for ev in _in_week(evs, now):
        item = {**ev, "days_until": days_until(ev, now)}
        if ev["type"] == "game":
            item.update(_squad_info(conn, fresh, ev))
        else:
            item["my_mark"] = marks.get(ev["id"])
            item["can_mark"] = (
                fresh.player_id is not None
                and ev["status"] != "cancelled"
                and not events.is_started(ev, now)
            )
        week.append(item)

    return {
        "next_event": next_event,
        "last_match": _last_match(conn, fresh, evs),
        "week": week,
        "stats": _my_stats(conn, fresh),
    }


def organizer_home(conn, actor, now=None):
    """Данные главного экрана организатора (FR-50). Только для организатора.

    needs_action — ближайшая будущая игра с неопубликованным составом или None
    (squad_players — игроки черновика).
    no_score — прошедшие игры без счёта (статус FR-48 «Счёт не внесён»).
    week_attendance — тренировки и мероприятия ближайших 7 дней со сводкой
    явки как в карточке события (FR-21): counts и missing — не ответившие.
    """
    fresh = auth.require_organizer(conn, actor)
    now = current_time(now)
    evs = events.list_events(conn, fresh, now)

    needs_action = None
    for ev in _upcoming(evs, now):
        if ev["type"] != "game" or ev["status"] == "cancelled":
            continue
        info = _squad_info(conn, fresh, ev)
        if not info["squad_published"]:
            needs_action = {**ev, **info, "days_until": days_until(ev, now)}
            break

    week_attendance = []
    for ev in _in_week(evs, now):
        if ev["type"] == "game" or ev["status"] == "cancelled":
            continue
        s = attendance.attendance_summary(conn, fresh, ev["id"], now)
        week_attendance.append({
            **ev,
            "days_until": days_until(ev, now),
            "counts": s["counts"],
            "missing": [p["name"] for p in s["by_status"]["none"]],
            "highlight_missing": s["highlight_missing"],
        })

    return {
        "needs_action": needs_action,
        "no_score": [
            {**e, "days_until": days_until(e, now)}
            for e in evs if e["display_status"] == "no_score"
        ],
        "week_attendance": week_attendance,
    }
