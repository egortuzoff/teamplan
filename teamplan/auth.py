"""Вход в систему, роли и проверка прав (FR-1 – FR-6, ТБ-1, ТБ-2, ТБ-3, ТБ-8)."""
import threading
import time
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
LOCKED_ERROR = (
    "Слишком много неудачных попыток входа. "
    "Вход под этим логином временно заблокирован, попробуйте через 15 минут."
)

# ТБ-8: после 5 неверных паролей подряд вход под логином блокируется на 15 минут.
MAX_FAILED_ATTEMPTS = 5
LOCK_SECONDS = 15 * 60

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


class LoginThrottle:
    """Счётчик неверных попыток входа по логину (ТБ-8).

    Хранится в памяти приложения и сбрасывается при перезапуске. Логин учитывается
    без регистра и независимо от того, существует ли он: поэтому блокировка
    не раскрывает, есть ли такой логин. Серия неверных попыток забывается, если
    новых не было LOCK_SECONDS. Один экземпляр общий для всех сессий Streamlit,
    они работают в разных потоках, поэтому доступ защищён блокировкой.
    clock — источник времени в секундах, в тестах подставляется.
    """

    def __init__(self, max_attempts=MAX_FAILED_ATTEMPTS, lock_seconds=LOCK_SECONDS,
                 clock=time.monotonic):
        self.max_attempts = max_attempts
        self.lock_seconds = lock_seconds
        self.clock = clock
        self._lock = threading.Lock()
        # логин -> [неверных попыток подряд, время последней, блокировка до]
        self._entries = {}

    @staticmethod
    def _key(login_name):
        return login_name.strip().lower()

    def _forget_old(self, now):
        """Убирает устаревшие записи, чтобы перебор логинов не занимал память."""
        old = [key for key, (_, last, until) in self._entries.items()
               if now - last >= self.lock_seconds and now >= until]
        for key in old:
            del self._entries[key]

    def is_locked(self, login_name):
        now = self.clock()
        with self._lock:
            entry = self._entries.get(self._key(login_name))
            if entry is None:
                return False
            if now < entry[2]:
                return True
            if entry[2] or now - entry[1] >= self.lock_seconds:
                # Блокировка истекла или серия устарела: попытки считаются заново.
                del self._entries[self._key(login_name)]
            return False

    def failed(self, login_name):
        now = self.clock()
        with self._lock:
            if len(self._entries) > 1000:
                self._forget_old(now)
            key = self._key(login_name)
            entry = self._entries.get(key)
            if entry is None or now - entry[1] >= self.lock_seconds:
                entry = [0, now, 0]
                self._entries[key] = entry
            entry[0] += 1
            entry[1] = now
            if entry[0] >= self.max_attempts:
                entry[2] = now + self.lock_seconds

    def succeeded(self, login_name):
        with self._lock:
            self._entries.pop(self._key(login_name), None)

    def reset(self):
        with self._lock:
            self._entries.clear()


login_throttle = LoginThrottle()


def login(conn, login_name, password, throttle=None):
    """Вход по логину и паролю (FR-1, FR-2, ТБ-8).

    При неверной паре сообщение одинаковое и не говорит, что именно неверно.
    Заблокированный логин (ТБ-8) не принимается и с верным паролем; сообщение
    о блокировке одинаково для существующего и несуществующего логина.
    """
    throttle = throttle or login_throttle
    login_name = (login_name or "").strip()
    if not login_name or not password:
        raise ServiceError("Введите логин и пароль.")
    if throttle.is_locked(login_name):
        raise ServiceError(LOCKED_ERROR)
    row = conn.execute(_USER_SQL + " WHERE lower(u.login) = lower(%s)", (login_name,)).fetchone()
    if row is None:
        _spend_time_like_real_check(password)
        throttle.failed(login_name)
        raise ServiceError(LOGIN_ERROR)
    if not verify_password(password, row["password_hash"]):
        throttle.failed(login_name)
        raise ServiceError(LOGIN_ERROR)
    throttle.succeeded(login_name)
    return _actor_from_row(row)


def require_actor(conn, actor):
    """Проверяет, что пользователь существует, и берёт его роль заново из базы."""
    if actor is None:
        raise ServiceError(SESSION_ERROR)
    row = conn.execute(_USER_SQL + " WHERE u.id = %s", (actor.user_id,)).fetchone()
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
        "SELECT 1 FROM users WHERE lower(login) = lower(%s)", (login_name,)
    ).fetchone() is not None


def insert_user(conn, login_name, password_hash, role, player_id, now=None):
    """Создаёт запись пользователя (вызывается внутри транзакции)."""
    if login_exists(conn, login_name):
        raise ServiceError("Такой логин уже занят.")
    ts = stamp(now)
    cur = conn.execute(
        "INSERT INTO users (login, password_hash, role, player_id, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (login_name, password_hash, role, player_id, ts, ts),
    )
    return cur.fetchone()["id"]


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
                "INSERT INTO players (name, created_at, updated_at) "
                "VALUES (%s, %s, %s) RETURNING id",
                (name, ts, ts),
            ).fetchone()["id"]
        return insert_user(conn, login_name, password_hash, ROLE_ORGANIZER, player_id, now)


def set_password(conn, user_id, new_password, now=None):
    """Задаёт новый пароль пользователю (вызывается организатором через players)."""
    password_hash = hash_password(new_password)
    with transaction(conn):
        cur = conn.execute(
            "UPDATE users SET password_hash = %s, updated_at = %s WHERE id = %s",
            (password_hash, stamp(now), user_id),
        )
        if cur.rowcount == 0:
            raise ServiceError("Аккаунт не найден.")
