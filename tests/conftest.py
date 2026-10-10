"""Общие заготовки для тестов: пустая база, организатор, игроки, фабрика событий."""
from datetime import datetime, time, timedelta

import pytest

from teamplan import auth, events, players
from teamplan.db import connect

# Фиксированное «сейчас», чтобы тесты не зависели от реальной даты.
NOW = datetime(2026, 11, 2, 12, 0)


@pytest.fixture
def conn():
    connection = connect(":memory:")
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
