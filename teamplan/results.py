"""Результат игры, голы и передачи (FR-27 – FR-31) и статистика (FR-32, FR-33)."""
from . import auth, notifications
from .common import current_time, event_start, stamp
from .db import transaction
from .errors import ServiceError
from .events import _get_event_row
from .players import list_players
from .settings import TEAM_NAME

MAX_SCORE = 99
OPPONENT_PLACEHOLDER = "Соперник"


def _check_score(value, label):
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_SCORE:
        raise ServiceError(f"{label}: целое число от 0 до {MAX_SCORE}.")
    return value


def _player_names(conn):
    return {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM players")}


def _load_result(conn, event_id):
    row = conn.execute(
        "SELECT our_score, their_score, created_at, updated_at FROM results "
        "WHERE event_id = ?", (event_id,),
    ).fetchone()
    if row is None:
        return None
    goals = [
        (g["scorer_id"], g["assist_id"])
        for g in conn.execute(
            "SELECT scorer_id, assist_id FROM goals WHERE event_id = ? ORDER BY id",
            (event_id,),
        )
    ]
    return {"our_score": row["our_score"], "their_score": row["their_score"],
            "goals": goals, "created_at": row["created_at"], "updated_at": row["updated_at"]}


def goals_mismatch(our_score, goals):
    """Предупреждение, если число голов не совпадает со счётом (FR-30), иначе None."""
    if len(goals) == our_score:
        return None
    return (
        f"Внимание: голов в списке {len(goals)}, а счёт нашей команды {our_score}. "
        "Результат сохранён, при необходимости исправьте его."
    )


def score_line(our_score, their_score, opponent=None):
    """Счёт в карточке игры (FR-47): «Счёт: 3 : 1 (наша команда : соперник)».

    Если соперник не указан, вместо названия пишется «Соперник».
    """
    return (
        f"Счёт: {our_score} : {their_score} "
        f"({TEAM_NAME} : {opponent or OPPONENT_PLACEHOLDER})"
    )


def goal_line(scorer_name, assist_name=None, escape=str):
    """Строка гола в Markdown (FR-47): автор жирным, передающий в скобках.

    escape — экранирование имён для Markdown; по умолчанию имена не меняются.
    """
    line = f"**{escape(scorer_name)}**"
    if assist_name:
        line += f" ({escape(assist_name)})"
    return line


def get_result(conn, actor, event_id):
    """Результат игры или None, если он не внесён. Виден всем вошедшим."""
    auth.require_actor(conn, actor)
    row = _get_event_row(conn, event_id)
    if row["type"] != "game":
        raise ServiceError("Результат вносится только для игр.")
    result = _load_result(conn, event_id)
    if result is None:
        return None
    names = _player_names(conn)
    result["goals"] = [
        {"scorer_id": s, "scorer_name": names.get(s, "?"),
         "assist_id": a, "assist_name": names.get(a) if a else None}
        for s, a in result["goals"]
    ]
    result["warning"] = goals_mismatch(result["our_score"], result["goals"])
    return result


def _normalize_goals(goals, squad):
    """Проверки гола (FR-28, FR-29): автор и передающий из состава и разные."""
    clean = []
    for number, goal in enumerate(goals or [], start=1):
        scorer, assist = goal
        if scorer is None or scorer not in squad:
            raise ServiceError(f"Гол {number}: автор должен входить в состав на эту игру.")
        if assist is not None and assist not in squad:
            raise ServiceError(f"Гол {number}: передающий должен входить в состав на эту игру.")
        if assist == scorer:
            raise ServiceError(f"Гол {number}: автор и передающий не могут совпадать.")
        clean.append((scorer, assist))
    return clean


def save_result(conn, actor, event_id, our_score, their_score, goals=(), now=None):
    """Вносит или изменяет результат игры целиком (FR-27 – FR-31).

    goals — список пар (автор, передающий или None); у гола не более одного
    передающего. Возвращает предупреждение FR-30 или None. Игроки из состава
    получают уведомление о внесении или изменении результата (п. 4.5).
    """
    auth.require_organizer(conn, actor)
    row = _get_event_row(conn, event_id)
    if row["type"] != "game":
        raise ServiceError("Результат вносится только для игр.")
    if row["status"] == "cancelled":
        raise ServiceError("Игра отменена, результат внести нельзя.")
    if event_start(row["date"], row["time"]) > current_time(now):
        raise ServiceError("Результат можно внести только после начала игры.")
    state = conn.execute(
        "SELECT state FROM squads WHERE event_id = ?", (event_id,)
    ).fetchone()
    if state is None or state["state"] != "published":
        raise ServiceError("Результат вносится для игры с опубликованным составом.")
    our_score = _check_score(our_score, "Счёт нашей команды")
    their_score = _check_score(their_score, "Счёт соперника")
    squad = set(notifications.squad_player_ids(conn, event_id))
    goals = _normalize_goals(goals, squad)
    warning = goals_mismatch(our_score, goals)

    old = _load_result(conn, event_id)
    if old and (old["our_score"], old["their_score"], old["goals"]) == (
        our_score, their_score, goals
    ):
        return warning  # ничего не изменилось: запись и уведомления не нужны

    with transaction(conn):
        ts = stamp(now)
        if old is None:
            conn.execute(
                "INSERT INTO results (event_id, our_score, their_score, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (event_id, our_score, their_score, ts, ts),
            )
        else:
            conn.execute(
                "UPDATE results SET our_score = ?, their_score = ?, updated_at = ? "
                "WHERE event_id = ?",
                (our_score, their_score, ts, event_id),
            )
        conn.execute("DELETE FROM goals WHERE event_id = ?", (event_id,))
        conn.executemany(
            "INSERT INTO goals (event_id, scorer_id, assist_id) VALUES (?, ?, ?)",
            [(event_id, scorer, assist) for scorer, assist in goals],
        )
        verb = "Внесён" if old is None else "Изменён"
        notifications.notify(
            conn, actor, squad, event_id, "result",
            f"{verb} результат игры {notifications.describe_event(row)}: "
            f"{our_score}:{their_score}.",
            now,
        )
    return warning


def _counts(conn, sql):
    return {row[0]: row[1] for row in conn.execute(sql)}


def player_stats(conn, actor):
    """Таблица «игрок, игры, голы, передачи» за всё время (FR-32, FR-33).

    «Игры» — число игр с внесённым счётом, в составе которых был игрок.
    Если ни у одной игры счёт не внесён, возвращается пустой список.
    """
    auth.require_actor(conn, actor)
    if conn.execute("SELECT 1 FROM results LIMIT 1").fetchone() is None:
        return []
    games = _counts(
        conn,
        "SELECT sp.player_id, COUNT(*) FROM squad_players sp "
        "JOIN results r ON r.event_id = sp.event_id GROUP BY sp.player_id",
    )
    scored = _counts(conn, "SELECT scorer_id, COUNT(*) FROM goals GROUP BY scorer_id")
    assists = _counts(
        conn,
        "SELECT assist_id, COUNT(*) FROM goals WHERE assist_id IS NOT NULL GROUP BY assist_id",
    )
    return [
        {"id": p["id"], "name": p["name"], "number": p["number"],
         "games": games.get(p["id"], 0), "goals": scored.get(p["id"], 0),
         "assists": assists.get(p["id"], 0)}
        for p in list_players(conn, actor)
    ]
