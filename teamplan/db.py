"""Подключение к PostgreSQL, схема данных и транзакции."""
import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from .errors import ServiceError

WRITE_ERROR = (
    "Не удалось сохранить данные. Ранее сохранённые данные не затронуты, "
    "повторите действие."
)

NO_DATABASE_URL = (
    "Не задана переменная окружения DATABASE_URL — адрес базы PostgreSQL, например "
    "postgresql://teamplan:teamplan-demo@localhost:5432/teamplan. Порядок запуска — в README."
)

# Ключи рекомендательных блокировок PostgreSQL (pg_advisory_xact_lock).
# SCHEMA_LOCK: таблицы создаёт только одно подключение, даже если несколько
# страниц открылись одновременно на пустой базе.
# SERIAL_LOCK: транзакции с serialized=True выполняются по очереди.
SCHEMA_LOCK = 7301
SERIAL_LOCK = 7302

# Схема создаётся сразу в актуальном виде (ТЗ 0.9): перенос данных из SQLite
# не выполняется (раздел 8). IF NOT EXISTS: на базе с данными при каждом
# подключении ничего не меняется.
SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        TEXT NOT NULL,
    number      INTEGER,
    position    TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id             INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    login          TEXT NOT NULL,
    password_hash  TEXT NOT NULL,
    role           TEXT NOT NULL CHECK (role IN ('organizer', 'player')),
    player_id      INTEGER UNIQUE REFERENCES players(id) ON DELETE SET NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    CHECK (role = 'organizer' OR player_id IS NOT NULL)
);

-- Логин уникален без учёта регистра (FR-4): Trener и trener — один логин.
CREATE UNIQUE INDEX IF NOT EXISTS users_login ON users (lower(login));

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type        TEXT NOT NULL CHECK (type IN ('training', 'game', 'activity')),
    title       TEXT NOT NULL,
    opponent    TEXT,
    date        TEXT NOT NULL,
    time        TEXT NOT NULL,
    gather_time TEXT,
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

CREATE TABLE IF NOT EXISTS results (
    event_id     INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE RESTRICT,
    our_score    INTEGER NOT NULL CHECK (our_score >= 0),
    their_score  INTEGER NOT NULL CHECK (their_score >= 0),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS goals (
    id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id    INTEGER NOT NULL REFERENCES results(event_id) ON DELETE CASCADE,
    scorer_id   INTEGER NOT NULL REFERENCES players(id) ON DELETE RESTRICT,
    assist_id   INTEGER REFERENCES players(id) ON DELETE RESTRICT,
    CHECK (assist_id IS NULL OR assist_id <> scorer_id)
);

-- attendance, reply_overdue, no_score — уведомления организатору (FR-52 – FR-54).
CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id    INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN (
                    'moved', 'cancelled', 'squad_published', 'squad_changed', 'result',
                    'attendance', 'reply_overdue', 'no_score')),
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    read_at     TEXT
);

CREATE INDEX IF NOT EXISTS notifications_user ON notifications (user_id, id);

-- Защита от дубликатов уведомлений «срок ответа прошёл» и «внесите счёт»
-- (FR-53 – FR-55) на уровне базы: одно такое уведомление на событие
-- и получателя даже при одновременных открытиях страниц.
CREATE UNIQUE INDEX IF NOT EXISTS notifications_once
    ON notifications (kind, event_id, user_id)
    WHERE kind IN ('reply_overdue', 'no_score');
"""

# Таблицы в порядке, в котором их можно очистить (seed.py --reset, тесты).
TABLES = (
    "notifications", "goals", "results", "squad_players", "squads",
    "attendance", "events", "users", "players",
)


def get_database_url():
    """Адрес базы из переменной окружения DATABASE_URL (ТР-7)."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(NO_DATABASE_URL)
    return url


def connect(url=None):
    """Открывает подключение и при необходимости создаёт таблицы.

    autocommit: чтение выполняется без открытой транзакции, изменения
    группируются в транзакции явно (см. transaction). Строки возвращаются
    словарями: row["name"].
    """
    conn = psycopg.connect(url or get_database_url(), autocommit=True, row_factory=dict_row)
    try:
        # Индекс notifications_once создаётся последним: если он есть, схема готова,
        # и обычное открытие страницы не выполняет команд CREATE.
        if conn.execute("SELECT to_regclass('notifications_once') AS name").fetchone()["name"]:
            return conn
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK,))
            conn.execute(SCHEMA)
    except BaseException:
        conn.close()
        raise
    return conn


def clear(conn):
    """Удаляет все данные и сбрасывает счётчики id (seed.py --reset, тесты)."""
    conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")


@contextmanager
def transaction(conn, serialized=False):
    """Выполняет изменения одной транзакцией.

    При любой ошибке изменения откатываются, ранее сохранённые данные
    не затрагиваются (ТС-3). Сбой базы превращается в понятное сообщение.
    serialized=True: одновременные транзакции с этим признаком выполняются
    по очереди — следующая ждёт, пока предыдущая завершится.
    """
    try:
        with conn.transaction():
            if serialized:
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (SERIAL_LOCK,))
            yield conn
    except psycopg.Error as exc:
        raise ServiceError(WRITE_ERROR) from exc
