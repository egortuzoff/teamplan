"""Вход в систему, роли и проверка прав (FR-1 – FR-6, ТБ-1, ТБ-2, ТБ-3)."""
from dataclasses import dataclass
from typing import Optional

import bcrypt

from .common import clean_text, stamp
from .db import transaction
from .errors import ServiceError

ROLE_ORGANIZER = "organizer"
ROLE_PLAYER = "player"
ROLE_LABELS = {ROLE_ORGANIZER: "Организатор", ROLE_PLAYER: "Игрок"}

# bcrypt учитывает только первые 72 байта пароля, поэтому более длинные отклоняем.
MAX_PASSWORD_BYTES = 72

LOGIN_ERROR = "Неверный логин или пароль."
SESSION_ERROR = "Сессия недействительна. Войдите в систему заново."
ORGANIZER_ONLY = "Это действие доступно только организатору."

_USER_SQL = (
    "SELECT u.id, u.login, u.password_hash, u.role, u.player_id, "
    "p.name AS player_name "
    "FROM users u LEFT JOIN players p ON p.id = u.player_id"
)


@dataclass(frozen=True)
class Actor:
    """Вошедший пользователь."""

    user_id: int
    login: str
    role: str
    player_id: Optional[int]
    name: str

    @property
    def is_organizer(self):
        return self.role == ROLE_ORGANIZER


def _password_bytes(password):
    if not isinstance(password, str) or password == "":
        raise ServiceError("Пароль не может быть пустым.")
    data = password.encode("utf-8")
    if len(data) > MAX_PASSWORD_BYTES:
        raise ServiceError(
            "Пароль слишком длинный: не более 72 байт "
            "(русская буква занимает 2 байта)."
        )
    return data


def hash_password(password):
    """Хеш пароля с солью (bcrypt). Пароль в открытом виде не сохраняется."""
    return bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password, password_hash):
    """Проверяет пароль по хешу; при любой неполадке возвращает False."""
    if not isinstance(password, str):
        return False
    data = password.encode("utf-8")
    if not data or len(data) > MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(data, password_hash.encode("ascii"))
    except ValueError:
        return False


_dummy_hash = None


def _spend_time_like_real_check(password):
    """Для неизвестного логина тратим то же время, что и на проверку пароля."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("dummy-password")
    verify_password(password, _dummy_hash)


def _actor_from_row(row):
    return Actor(
        user_id=row["id"],
        login=row["login"],
        role=row["role"],
        player_id=row["player_id"],
        name=row["player_name"] or row["login"],
    )


def clean_login(value):
    """Логин: обязателен, без пробелов, не длиннее 50 символов."""
    text = clean_text(value, "Логин", 50)
    if any(ch.isspace() for ch in text):
        raise ServiceError("Логин: не должен содержать пробелов.")
    return text


def login(conn, login_name, password):
    """Вход по логину и паролю (FR-1, FR-2).

    При неверной паре сообщение одинаковое и не говорит, что именно неверно.
    """
    login_name = (login_name or "").strip()
    if not login_name or not password:
        raise ServiceError("Введите логин и пароль.")
    row = conn.execute(_USER_SQL + " WHERE u.login = ?", (login_name,)).fetchone()
    if row is None:
        _spend_time_like_real_check(password)
        raise ServiceError(LOGIN_ERROR)
    if not verify_password(password, row["password_hash"]):
        raise ServiceError(LOGIN_ERROR)
    return _actor_from_row(row)


def require_actor(conn, actor):
    """Проверяет, что пользователь существует, и берёт его роль заново из базы."""
    if actor is None:
        raise ServiceError(SESSION_ERROR)
    row = conn.execute(_USER_SQL + " WHERE u.id = ?", (actor.user_id,)).fetchone()
    if row is None:
        raise ServiceError(SESSION_ERROR)
    return _actor_from_row(row)


def require_organizer(conn, actor):
    """Права проверяются при каждой операции, а не только скрытием кнопок (ТБ-1)."""
    fresh = require_actor(conn, actor)
    if not fresh.is_organizer:
        raise ServiceError(ORGANIZER_ONLY)
    return fresh


def login_exists(conn, login_name):
    return conn.execute(
        "SELECT 1 FROM users WHERE login = ?", (login_name,)
    ).fetchone() is not None


def insert_user(conn, login_name, password_hash, role, player_id, now=None):
    """Создаёт запись пользователя (вызывается внутри транзакции)."""
    if login_exists(conn, login_name):
        raise ServiceError("Такой логин уже занят.")
    ts = stamp(now)
    cur = conn.execute(
        "INSERT INTO users (login, password_hash, role, player_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (login_name, password_hash, role, player_id, ts, ts),
    )
    return cur.lastrowid


def create_organizer(conn, login_name, password, player_name=None, now=None):
    """Создаёт аккаунт организатора (используется скриптами seed.py и create_organizer.py).

    Если указано player_name, организатор одновременно становится игроком (FR-6).
    """
    login_name = clean_login(login_name)
    password_hash = hash_password(password)
    name = clean_text(player_name, "Имя игрока", 100, required=False)
    with transaction(conn):
        player_id = None
        if name:
            ts = stamp(now)
            player_id = conn.execute(
                "INSERT INTO players (name, created_at, updated_at) VALUES (?, ?, ?)",
                (name, ts, ts),
            ).lastrowid
        return insert_user(conn, login_name, password_hash, ROLE_ORGANIZER, player_id, now)


def set_password(conn, user_id, new_password, now=None):
    """Задаёт новый пароль пользователю (вызывается организатором через players)."""
    password_hash = hash_password(new_password)
    with transaction(conn):
        cur = conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (password_hash, stamp(now), user_id),
        )
        if cur.rowcount == 0:
            raise ServiceError("Аккаунт не найден.")
