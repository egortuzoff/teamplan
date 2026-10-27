"""Создаёт тестовые данные (п. 7.3 ТЗ): организатора, трёх вымышленных игроков и события.

Запуск:   python seed.py
Заново:   python seed.py --reset   (удаляет все данные из базы и создаёт тестовые с нуля)

База задаётся переменной окружения DATABASE_URL (см. README).
Только для демонстрационного режима: при TEAMPLAN_MODE=production скрипт отказывается
работать (РЗ-7).

Все имена, логины и пароли вымышленные. Даты событий считаются от сегодняшнего дня.
Сыгранная игра со счётом 2:1 и игра без счёта создаются в прошлом: такое возможно
только здесь, в интерфейсе событие в прошлом создать нельзя.
"""
import argparse
import sys
from datetime import datetime, time, timedelta

from teamplan import attendance, auth, events, players, results, squads
from teamplan.db import clear, connect
from teamplan.errors import ServiceError
from teamplan.settings import MODE_PRODUCTION, get_mode

ORGANIZER = {"login": "trener", "password": "trener-demo-1"}
PLAYERS = [
    {"name": "Игрок Первый", "login": "igrok1", "password": "igrok1-demo", "number": 10, "position": "нападающий"},
    {"name": "Игрок Второй", "login": "igrok2", "password": "igrok2-demo", "number": 7, "position": "защитник"},
    {"name": "Игрок Третий", "login": "igrok3", "password": "igrok3-demo", "number": 1, "position": "вратарь"},
]
PLAYED_GAME_TITLE = "Сыгранная игра с командой «Прошлый соперник»"
# Соперники вымышленные (ТБ-5).
PLAYED_GAME_OPPONENT = "Прошлый соперник"
FUTURE_GAME_OPPONENT = "Тестовые ястребы"
# Для главного экрана организатора (AC-19): игра с неопубликованным составом
# и прошедшая игра без счёта.
DRAFT_GAME_OPPONENT = "Тестовые медведи"
DRAFT_GAME_TITLE = f"Игра с командой «{DRAFT_GAME_OPPONENT}»"
NO_SCORE_GAME_OPPONENT = "Тестовая звезда"
NO_SCORE_GAME_TITLE = f"Игра с командой «{NO_SCORE_GAME_OPPONENT}»"


def add_played_game(conn, org, first_id, second_id):
    """Сыгранная игра 3 дня назад: состав из двух игроков, счёт 2:1.

    Голы: Игрок Первый с передачей Игрока Второго и Игрок Первый без передачи.
    Событие создаётся с «текущим временем» за неделю до сегодняшнего дня, поэтому
    проверка даты в прошлом его пропускает.
    """
    today = datetime.now().date()
    week_ago = datetime.combine(today - timedelta(days=7), time(12, 0))
    game = events.create_event(
        conn, org, "game", PLAYED_GAME_TITLE, today - timedelta(days=3), time(19, 30),
        "Стадион", gather_time=time(18, 45), now=week_ago, opponent=PLAYED_GAME_OPPONENT,
    )
    squads.save_squad(conn, org, game, [first_id, second_id], now=week_ago)
    squads.publish_squad(conn, org, game, now=week_ago)
    results.save_result(conn, org, game, 2, 1, [(first_id, second_id), (first_id, None)])
    return game


def add_draft_game(conn, org, first_id, second_id):
    """Будущая игра через 12 дней с черновиком состава (игроки его не видят)."""
    today = datetime.now().date()
    game = events.create_event(
        conn, org, "game", DRAFT_GAME_TITLE, today + timedelta(days=12), time(19, 30),
        "Стадион", gather_time=time(18, 45), opponent=DRAFT_GAME_OPPONENT,
    )
    squads.save_squad(conn, org, game, [first_id, second_id])
    return game


def add_no_score_game(conn, org, first_id, second_id):
    """Игра вчера с опубликованным составом, счёт не внесён («Счёт не внесён», FR-48)."""
    today = datetime.now().date()
    week_ago = datetime.combine(today - timedelta(days=7), time(12, 0))
    game = events.create_event(
        conn, org, "game", NO_SCORE_GAME_TITLE, today - timedelta(days=1), time(19, 30),
        "Стадион", gather_time=time(18, 45), now=week_ago, opponent=NO_SCORE_GAME_OPPONENT,
    )
    squads.save_squad(conn, org, game, [first_id, second_id], now=week_ago)
    squads.publish_squad(conn, org, game, now=week_ago)
    return game


# Игры, которые дополняют базу с тестовыми данными прежних версий: название и функция.
TOP_UP_GAMES = [
    (PLAYED_GAME_TITLE, "add_played_game"),
    (DRAFT_GAME_TITLE, "add_draft_game"),
    (NO_SCORE_GAME_TITLE, "add_no_score_game"),
]


def _player_id(conn, login_name):
    row = conn.execute("SELECT player_id FROM users WHERE lower(login) = lower(%s)", (login_name,)).fetchone()
    return row["player_id"] if row else None


def top_up(conn):
    """Добавляет недостающие тестовые игры в базу с тестовыми данными прежней версии.

    Срабатывает, только если в базе есть тестовые аккаунты из этого файла.
    Добавляются только игры, которых ещё нет (по названию). Другие данные
    не изменяются. Возвращает названия добавленных игр.
    """
    missing = [
        (title, func) for title, func in TOP_UP_GAMES
        if not conn.execute("SELECT 1 FROM events WHERE title = %s", (title,)).fetchone()
    ]
    if not missing:
        return []
    first, second = _player_id(conn, PLAYERS[0]["login"]), _player_id(conn, PLAYERS[1]["login"])
    if first is None or second is None:
        return []
    try:
        org = auth.login(conn, ORGANIZER["login"], ORGANIZER["password"])
    except ServiceError:
        return []
    if not org.is_organizer:
        return []
    for title, func in missing:
        globals()[func](conn, org, first, second)
    return [title for title, _ in missing]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Тестовые данные TeamPlan")
    parser.add_argument("--reset", action="store_true", help="удалить все данные из базы и создать тестовые заново")
    args = parser.parse_args(argv)

    # РЗ-7: в рабочем режиме тестовые данные не загружаются, в том числе вручную.
    try:
        mode = get_mode()
    except RuntimeError as exc:
        print(exc)
        return 1
    if mode == MODE_PRODUCTION:
        print(
            "Рабочий режим (TEAMPLAN_MODE=production): тестовые данные не создаются. "
            "Организатор создаётся командой python create_organizer.py."
        )
        return 1

    try:
        conn = connect()
    except RuntimeError as exc:  # не задан DATABASE_URL
        print(exc)
        return 1
    if args.reset:
        clear(conn)
    if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        added = top_up(conn)
        if added:
            print(
                "В базу с тестовыми данными добавлены игры: " + "; ".join(added)
                + ". Остальные данные не изменены."
            )
        else:
            print("В базе уже есть данные, они не изменены.")
        print(
            "Чтобы создать тестовые данные заново:\n"
            "  без Docker: python seed.py --reset\n"
            "  в Docker:   docker compose down -v, затем docker compose up --build"
        )
        conn.close()
        return 0

    auth.create_organizer(conn, ORGANIZER["login"], ORGANIZER["password"])
    org = auth.login(conn, ORGANIZER["login"], ORGANIZER["password"])

    ids = []
    for p in PLAYERS:
        ids.append(
            players.create_player_account(
                conn, org, p["name"], p["login"], p["password"], p["number"], p["position"]
            )
        )

    today = datetime.now().date()
    training = events.create_event(
        conn, org, "training", "Тренировка", today + timedelta(days=2), time(19, 0),
        "Основной зал", reply_by=datetime.combine(today + timedelta(days=1), time(18, 0)),
        gather_time=time(18, 30),
    )
    events.create_event(
        conn, org, "activity", "Выезд на базу отдыха", today + timedelta(days=9), time(10, 0),
        "База отдыха", gather_time=time(9, 30),
    )
    game = events.create_event(
        conn, org, "game", f"Игра с командой «{FUTURE_GAME_OPPONENT}»", today + timedelta(days=5),
        time(19, 30), "Стадион", gather_time=time(18, 45), opponent=FUTURE_GAME_OPPONENT,
    )
    squads.save_squad(conn, org, game, [ids[0], ids[1]])
    squads.publish_squad(conn, org, game)
    add_played_game(conn, org, ids[0], ids[1])
    add_draft_game(conn, org, ids[0], ids[1])
    add_no_score_game(conn, org, ids[0], ids[1])

    first = auth.login(conn, PLAYERS[0]["login"], PLAYERS[0]["password"])
    attendance.set_attendance(conn, first, training, first.player_id, "yes")

    print("Тестовые данные созданы.\n")
    print("Аккаунты (все вымышленные):")
    print(f"  организатор: {ORGANIZER['login']} / {ORGANIZER['password']}")
    for p in PLAYERS:
        print(f"  игрок:       {p['login']} / {p['password']}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
