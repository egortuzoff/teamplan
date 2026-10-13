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

# Таблица событий описана отдельно: по этому же описанию она пересоздаётся
# при обновлении базы старого формата (_upgrade_events).
EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS {name} (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
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
"""

# Таблицы создаются через IF NOT EXISTS: при запуске на базе прежней версии
# недостающие таблицы (результаты, голы, уведомления) добавляются, данные
# существующих таблиц не затрагиваются.
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

{events_table}

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
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    INTEGER NOT NULL REFERENCES results(event_id) ON DELETE CASCADE,
    scorer_id   INTEGER NOT NULL REFERENCES players(id) ON DELETE RESTRICT,
    assist_id   INTEGER REFERENCES players(id) ON DELETE RESTRICT,
    CHECK (assist_id IS NULL OR assist_id <> scorer_id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id    INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN (
                    'moved', 'cancelled', 'squad_published', 'squad_changed', 'result')),
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    read_at     TEXT
);

CREATE INDEX IF NOT EXISTS notifications_user ON notifications (user_id, id);
""".replace("{events_table}", EVENTS_TABLE.format(name="events").strip())


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
    conn.executescript(SCHEMA)
    _upgrade_events(conn)
    _add_opponent(conn)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _upgrade_events(conn):
    """Обновляет таблицу событий из формата ТЗ 0.1 без потери данных.

    В старом формате тип «сбор» (camp) и нет поля «время сбора». Ограничение
    CHECK в SQLite не изменить, поэтому таблица пересоздаётся: данные
    копируются, события camp становятся activity (мероприятие). Вызывается
    до включения внешних ключей, чтобы удаление старой таблицы не удалило
    отметки явки и составы.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
    if "gather_time" in columns:
        return
    conn.execute("BEGIN")
    try:
        conn.execute(EVENTS_TABLE.format(name="events_new"))
        conn.execute(
            "INSERT INTO events_new (id, type, title, opponent, date, time, gather_time, "
            "place, status, reply_by, created_at, updated_at) "
            "SELECT id, CASE type WHEN 'camp' THEN 'activity' ELSE type END, title, NULL, "
            "date, time, NULL, place, status, reply_by, created_at, updated_at "
            "FROM events"
        )
        conn.execute("DROP TABLE events")
        conn.execute("ALTER TABLE events_new RENAME TO events")
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise sqlite3.IntegrityError("foreign key check failed")
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def _add_opponent(conn):
    """Добавляет поле «Соперник» (ТЗ 0.6) в базу прежней версии.

    Столбец без значения по умолчанию добавляется без пересоздания таблицы,
    существующие события получают пустого соперника.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
    if "opponent" not in columns:
        conn.execute("ALTER TABLE events ADD COLUMN opponent TEXT")


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
