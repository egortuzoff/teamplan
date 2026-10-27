"""Общие заготовки для тестов: пустая база, организатор, игроки, фабрика событий.

Тесты работают с PostgreSQL по адресу из переменной TEST_DATABASE_URL (ТП-5).
Каждый тест начинается с пустой базы: все данные в ней удаляются.
"""
import os
from datetime import datetime, time, timedelta

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from teamplan import auth, events, players
from teamplan.db import clear, connect

# Фиксированное «сейчас», чтобы тесты не зависели от реальной даты.
NOW = datetime(2026, 11, 2, 12, 0)

NO_TEST_DATABASE = """Не задана переменная TEST_DATABASE_URL — адрес базы PostgreSQL для тестов.
Тесты удаляют из этой базы все данные, поэтому имя базы должно содержать «test».

Запустите PostgreSQL и задайте переменную (подробнее — в README, раздел о тестах):
  docker compose up -d db
  PowerShell:  $env:TEST_DATABASE_URL = "postgresql://teamplan:teamplan-demo@localhost:5432/teamplan_test"
  bash:        export TEST_DATABASE_URL=postgresql://teamplan:teamplan-demo@localhost:5432/teamplan_test
  python -m pytest

Базы teamplan_test может не быть: тесты создадут её сами."""


def pytest_configure(config):
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url:
        raise pytest.UsageError(NO_TEST_DATABASE)
    if "test" not in (conninfo_to_dict(url).get("dbname") or ""):
        raise pytest.UsageError(
            "TEST_DATABASE_URL: имя базы должно содержать «test» — тесты удаляют из неё "
            "все данные, а рабочую или демонстрационную базу так легко очистить по ошибке."
        )


def _create_database_if_missing(url):
    """Создаёт базу для тестов, если её нет (подключение к служебной базе postgres)."""
    try:
        psycopg.connect(url).close()
        return
    except psycopg.OperationalError as exc:
        if "does not exist" not in str(exc):
            raise
    name = conninfo_to_dict(url)["dbname"]
    with psycopg.connect(make_conninfo(url, dbname="postgres"), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def test_database_url():
    url = os.environ["TEST_DATABASE_URL"].strip()
    _create_database_if_missing(url)
    return url


@pytest.fixture(autouse=True)
def database_url(test_database_url, monkeypatch):
    """Пустая база для каждого теста; DATABASE_URL приложения указывает на неё."""
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    # Тесты по умолчанию в демонстрационном режиме, даже если TEAMPLAN_MODE задан в терминале.
    monkeypatch.delenv("TEAMPLAN_MODE", raising=False)
    connection = connect(test_database_url)
    try:
        clear(connection)
    finally:
        connection.close()
    return test_database_url


@pytest.fixture(autouse=True)
def fresh_login_throttle():
    """Счётчик неверных входов (ТБ-8) общий для процесса: каждый тест начинает с чистого."""
    auth.login_throttle.reset()
    yield
    auth.login_throttle.reset()


@pytest.fixture
def conn(database_url):
    connection = connect(database_url)
    yield connection
    connection.close()


@pytest.fixture
def now():
    return NOW


@pytest.fixture
def org(conn):
    auth.create_organizer(conn, "trener", "pass-org")
    return auth.login(conn, "trener", "pass-org")


def _make_player(conn, org, name, login, number):
    players.create_player_account(conn, org, name, login, "pass-" + login, number)
    return auth.login(conn, login, "pass-" + login)


@pytest.fixture
def p1(conn, org):
    return _make_player(conn, org, "Игрок Первый", "igrok1", 10)


@pytest.fixture
def p2(conn, org):
    return _make_player(conn, org, "Игрок Второй", "igrok2", 7)


@pytest.fixture
def p3(conn, org):
    return _make_player(conn, org, "Игрок Третий", "igrok3", 1)


@pytest.fixture
def mk_event(conn, org, now):
    """Создаёт событие: mk_event("game", days=3, hour=19). По умолчанию тренировка через 3 дня."""

    def make(event_type="training", days=3, hour=19, title="Событие", **kwargs):
        day = now.date() + timedelta(days=days)
        return events.create_event(
            conn, org, event_type, title, day, time(hour, 0), now=now, **kwargs
        )

    return make


@pytest.fixture
def played_game(conn, org, p1, p2, now):
    """Игра, которая уже прошла (2 дня назад), с опубликованным составом из p1 и p2.

    Создаётся с «текущим временем» за неделю до now, как в seed.py.
    """
    from teamplan import squads

    week_ago = now - timedelta(days=7)
    game = events.create_event(
        conn, org, "game", "Прошедшая игра", now.date() - timedelta(days=2), time(19, 0),
        now=week_ago,
    )
    squads.save_squad(conn, org, game, [p1.player_id, p2.player_id], now=week_ago)
    squads.publish_squad(conn, org, game, now=week_ago)
    return game


def notes(conn, actor):
    """Тексты уведомлений пользователя, новые сверху."""
    from teamplan import notifications

    return [n["text"] for n in notifications.list_notifications(conn, actor)]
