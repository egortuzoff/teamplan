"""Создаёт аккаунт организатора для реальной работы (без тестовых данных).

Запуск: python create_organizer.py
Пароль вводится скрыто и нигде не сохраняется в открытом виде.
"""
import getpass
import sys

from teamplan import auth
from teamplan.db import connect
from teamplan.errors import ServiceError


def main():
    login_name = input("Логин организатора: ").strip()
    password = getpass.getpass("Пароль (при вводе не отображается): ")
    repeat = getpass.getpass("Повторите пароль: ")
    if password != repeat:
        print("Пароли не совпадают.")
        return 1
    conn = connect()
    try:
        auth.create_organizer(conn, login_name, password)
    except ServiceError as exc:
        print(f"Не удалось создать аккаунт: {exc}")
        return 1
    finally:
        conn.close()
    print("Аккаунт организатора создан.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
