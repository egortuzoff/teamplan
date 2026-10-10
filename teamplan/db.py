"""Подключение к SQLite, схема данных и транзакции."""
import os
import sqlite3
from contextlib import contextmanager

from .errors import ServiceError

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(PROJECT_ROOT, "data", "teamplan.db")

WRITE_ERROR = (
    "Не удалось сохранить данные. Ранее сохранённые данные не затронуты, "
    "повторите действие."
)

# Схема рассчитана на этап 2: таблицы результатов, голов и уведомлений
# добавляются отдельными таблицами без переделки существующих.
SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    number      INTEGER,
    position    TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    login          TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash  TEXT NOT NULL,
    role           TEXT NOT NULL CHECK (role IN ('organizer', 'player')),
    player_id      INTEGER UNIQUE REFERENCES players(id) ON DELETE SET NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    CHECK (role = 'organizer' OR player_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL CHECK (type IN ('training', 'camp', 'game')),
    title       TEXT NOT NULL,
    date        TEXT NOT NULL,
    time        TEXT NOT NULL,
    place       TEXT,
    status      TEXT NOT NULL DEFAULT 'planned'
                CHECK (status IN ('planned', 'cancelled')),
    reply_by    TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attendance (
    event_id    INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    player_id   INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    status      TEXT NOT NULL CHECK (status IN ('yes', 'no', 'maybe')),
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (event_id, player_id)
);

CREATE TABLE IF NOT EXISTS squads (
    event_id    INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
    state       TEXT NOT NULL CHECK (state IN ('draft', 'published')),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS squad_players (
    event_id    INTEGER NOT NULL REFERENCES squads(event_id) ON DELETE CASCADE,
    player_id   INTEGER NOT NULL REFERENCES players(id) ON DELETE RESTRICT,
    PRIMARY KEY (event_id, player_id)
);
"""


def get_db_path():
    """Путь к файлу базы: переменная TEAMPLAN_DB или data/teamplan.db."""
    return os.environ.get("TEAMPLAN_DB") or DEFAULT_DB_PATH


def connect(path=None):
    """Открывает базу и при необходимости создаёт таблицы."""
    path = path or get_db_path()
    if path != ":memory:":
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
    # isolation_level=None: транзакциями управляем вручную (см. transaction).
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def transaction(conn):
    """Выполняет изменения одной транзакцией.

    При любой ошибке изменения откатываются, ранее сохранённые данные
    не затрагиваются (ТС-3). Сбой базы превращается в понятное сообщение.
    """
    conn.execute("BEGIN")
    try:
        yield conn
    except ServiceError:
        conn.rollback()
        raise
    except sqlite3.Error as exc:
        conn.rollback()
        raise ServiceError(WRITE_ERROR) from exc
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()
