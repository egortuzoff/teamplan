"""Создаёт тестовые данные (п. 7.3 ТЗ): организатора, трёх вымышленных игроков и события.

Запуск:   python seed.py
Заново:   python seed.py --reset   (удаляет файл базы и создаёт данные с нуля)

Все имена, логины и пароли вымышленные. Даты событий считаются от сегодняшнего дня.
Сыгранная игра со счётом 2:1 создаётся в прошлом: такое возможно только здесь,
в интерфейсе событие в прошлом создать нельзя.
"""
import argparse
import os
import sys
from datetime import datetime, time, timedelta

from teamplan import attendance, auth, events, players, results, squads
from teamplan.db import connect, get_db_path
from teamplan.errors import ServiceError

ORGANIZER = {"login": "trener", "password": "trener-demo-1"}
PLAYERS = [
    {"name": "Игрок Первый", "login": "igrok1", "password": "igrok1-demo", "number": 10, "position": "нападающий"},
    {"name": "Игрок Второй", "login": "igrok2", "password": "igrok2-demo", "number": 7, "position": "защитник"},
    {"name": "Игрок Третий", "login": "igrok3", "password": "igrok3-demo", "number": 1, "position": "вратарь"},
]
PLAYED_GAME_TITLE = "Сыгранная игра с командой «Прошлый соперник»"


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
        "Стадион", gather_time=time(18, 45), now=week_ago,
    )
    squads.save_squad(conn, org, game, [first_id, second_id], now=week_ago)
    squads.publish_squad(conn, org, game, now=week_ago)
    results.save_result(conn, org, game, 2, 1, [(first_id, second_id), (first_id, None)])
    return game


def _player_id(conn, login_name):
    row = conn.execute("SELECT player_id FROM users WHERE login = ?", (login_name,)).fetchone()
    return row["player_id"] if row else None


def top_up(conn):
    """Добавляет сыгранную игру в базу с тестовыми данными прежней версии.

    Срабатывает, только если в базе есть тестовые аккаунты из этого файла
    и сыгранной игры ещё нет. Другие данные не изменяются.
    """
    if conn.execute("SELECT 1 FROM events WHERE title = ?", (PLAYED_GAME_TITLE,)).fetchone():
        return False
    first, second = _player_id(conn, PLAYERS[0]["login"]), _player_id(conn, PLAYERS[1]["login"])
    if first is None or second is None:
        return False
    try:
        org = auth.login(conn, ORGANIZER["login"], ORGANIZER["password"])
    except ServiceError:
        return False
    if not org.is_organizer:
        return False
    add_played_game(conn, org, first, second)
    return True


def main():
    parser = argparse.ArgumentParser(description="Тестовые данные TeamPlan")
    parser.add_argument("--reset", action="store_true", help="удалить базу и создать данные заново")
    args = parser.parse_args()

    db_path = get_db_path()
    if args.reset and os.path.exists(db_path):
        os.remove(db_path)

    conn = connect()
    if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        if top_up(conn):
            print(
                "В базу с тестовыми данными добавлена сыгранная игра со счётом 2:1, "
                "остальные данные не изменены."
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
        conn, org, "game", "Игра с командой «Соперник»", today + timedelta(days=5), time(19, 30),
        "Стадион", gather_time=time(18, 45),
    )
    squads.save_squad(conn, org, game, [ids[0], ids[1]])
    squads.publish_squad(conn, org, game)
    add_played_game(conn, org, ids[0], ids[1])

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
