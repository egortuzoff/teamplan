"""Создаёт тестовые данные: организатора, трёх вымышленных игроков и три события.

Запуск:   python seed.py
Заново:   python seed.py --reset   (удаляет файл базы и создаёт данные с нуля)

Все имена, логины и пароли вымышленные. Даты событий считаются от сегодняшнего дня.
"""
import argparse
import os
import sys
from datetime import datetime, time, timedelta

from teamplan import attendance, auth, events, players, squads
from teamplan.db import connect, get_db_path

ORGANIZER = {"login": "trener", "password": "trener-demo-1"}
PLAYERS = [
    {"name": "Игрок Первый", "login": "igrok1", "password": "igrok1-demo", "number": 10, "position": "нападающий"},
    {"name": "Игрок Второй", "login": "igrok2", "password": "igrok2-demo", "number": 7, "position": "защитник"},
    {"name": "Игрок Третий", "login": "igrok3", "password": "igrok3-demo", "number": 1, "position": "вратарь"},
]


def main():
    parser = argparse.ArgumentParser(description="Тестовые данные TeamPlan")
    parser.add_argument("--reset", action="store_true", help="удалить базу и создать данные заново")
    args = parser.parse_args()

    db_path = get_db_path()
    if args.reset and os.path.exists(db_path):
        os.remove(db_path)

    conn = connect()
    if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        print("В базе уже есть данные. Чтобы создать их заново, выполните: python seed.py --reset")
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
    )
    events.create_event(
        conn, org, "camp", "Выездной сбор", today + timedelta(days=9), time(10, 0), "База отдыха"
    )
    game = events.create_event(
        conn, org, "game", "Игра с командой «Соперник»", today + timedelta(days=5), time(19, 30),
        "Стадион",
    )
    squads.save_squad(conn, org, game, [ids[0], ids[1]])
    squads.publish_squad(conn, org, game)

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
