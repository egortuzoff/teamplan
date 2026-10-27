"""Подготовка базы при запуске контейнера, до старта Streamlit (см. Dockerfile).

Режим задаётся переменной окружения TEAMPLAN_MODE (ТР-6):
  demo (по умолчанию)  тестовые данные, как при ручном запуске python seed.py;
  production           тестовые данные не создаются (РЗ-7). Если в базе нет ни одного
                       организатора, в журнал контейнера выводится подсказка,
                       как его создать (create_organizer.py).
"""
import sys

import seed
from teamplan import auth
from teamplan.db import connect
from teamplan.settings import MODE_DEMO, get_mode

NO_ORGANIZER_HINT = """Рабочий режим (TEAMPLAN_MODE=production): тестовые данные не создаются.
В базе нет ни одного организатора, войти в приложение пока некому.
Создайте аккаунт организатора командой на сервере (в папке проекта):
  docker compose exec teamplan python create_organizer.py"""


def has_organizer(conn):
    return conn.execute(
        "SELECT 1 FROM users WHERE role = %s LIMIT 1", (auth.ROLE_ORGANIZER,)
    ).fetchone() is not None


def main():
    try:
        mode = get_mode()
    except RuntimeError as exc:
        print(exc)
        return 1
    if mode == MODE_DEMO:
        return seed.main([])
    try:
        conn = connect()  # создаёт таблицы в пустой базе
    except RuntimeError as exc:  # не задан DATABASE_URL
        print(exc)
        return 1
    try:
        if has_organizer(conn):
            print("Рабочий режим (TEAMPLAN_MODE=production): тестовые данные не создаются.")
        else:
            print(NO_ORGANIZER_HINT)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
