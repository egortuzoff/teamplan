"""Ограничение попыток входа (ТБ-8, AC-26). Время подставляется, ожидания нет."""
import threading

import pytest

from teamplan import auth
from teamplan.errors import ServiceError


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def throttle(clock):
    return auth.LoginThrottle(clock=clock)


def login_error(conn, login_name, password, throttle):
    with pytest.raises(ServiceError) as exc:
        auth.login(conn, login_name, password, throttle=throttle)
    return str(exc.value)


def fail(conn, throttle, login_name="trener", times=auth.MAX_FAILED_ATTEMPTS):
    for _ in range(times):
        assert login_error(conn, login_name, "wrong", throttle) == auth.LOGIN_ERROR


def test_five_wrong_passwords_lock_even_correct_password(conn, org, throttle):
    fail(conn, throttle)
    assert login_error(conn, "trener", "pass-org", throttle) == auth.LOCKED_ERROR
    assert login_error(conn, "TRENER", "pass-org", throttle) == auth.LOCKED_ERROR


def test_four_wrong_passwords_do_not_lock(conn, org, throttle):
    fail(conn, throttle, times=auth.MAX_FAILED_ATTEMPTS - 1)
    assert auth.login(conn, "trener", "pass-org", throttle=throttle).login == "trener"


def test_lock_lasts_15_minutes(conn, org, throttle, clock):
    fail(conn, throttle)
    clock.advance(auth.LOCK_SECONDS - 1)
    assert login_error(conn, "trener", "pass-org", throttle) == auth.LOCKED_ERROR
    clock.advance(1)
    assert auth.login(conn, "trener", "pass-org", throttle=throttle).login == "trener"


def test_after_lock_expires_attempts_count_again(conn, org, throttle, clock):
    fail(conn, throttle)
    clock.advance(auth.LOCK_SECONDS)
    fail(conn, throttle, times=auth.MAX_FAILED_ATTEMPTS - 1)
    assert not throttle.is_locked("trener")
    fail(conn, throttle, times=1)
    assert throttle.is_locked("trener")


def test_attempts_during_lock_do_not_extend_it(conn, org, throttle, clock):
    fail(conn, throttle)
    clock.advance(auth.LOCK_SECONDS - 60)
    login_error(conn, "trener", "wrong", throttle)
    clock.advance(60)
    assert auth.login(conn, "trener", "pass-org", throttle=throttle)


def test_success_resets_counter(conn, org, throttle):
    fail(conn, throttle, times=auth.MAX_FAILED_ATTEMPTS - 1)
    auth.login(conn, "trener", "pass-org", throttle=throttle)
    fail(conn, throttle, times=auth.MAX_FAILED_ATTEMPTS - 1)
    assert auth.login(conn, "trener", "pass-org", throttle=throttle)


def test_messages_same_for_existing_and_missing_login(conn, org, throttle):
    fail(conn, throttle, "trener")
    fail(conn, throttle, "nobody")
    existing = login_error(conn, "trener", "pass-org", throttle)
    missing = login_error(conn, "nobody", "pass-org", throttle)
    assert existing == missing == auth.LOCKED_ERROR
    assert "trener" not in existing and "nobody" not in missing


def test_lock_is_per_login(conn, org, p1, throttle):
    fail(conn, throttle, "trener")
    assert auth.login(conn, "igrok1", "pass-igrok1", throttle=throttle).login == "igrok1"


def test_empty_fields_are_not_counted(conn, org, throttle):
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        assert login_error(conn, "trener", "", throttle) == "Введите логин и пароль."
    assert auth.login(conn, "trener", "pass-org", throttle=throttle)


def test_old_failures_are_forgotten(conn, org, throttle, clock):
    fail(conn, throttle, times=auth.MAX_FAILED_ATTEMPTS - 1)
    clock.advance(auth.LOCK_SECONDS)
    fail(conn, throttle, times=1)
    assert not throttle.is_locked("trener")


def test_default_throttle_is_used_by_login(conn, org):
    fail(conn, auth.login_throttle)
    with pytest.raises(ServiceError, match="заблокирован"):
        auth.login(conn, "trener", "pass-org")


def test_counter_is_thread_safe(clock):
    throttle = auth.LoginThrottle(max_attempts=1000, clock=clock)
    threads = [
        threading.Thread(target=lambda: [throttle.failed("trener") for _ in range(100)])
        for _ in range(10)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Ровно 1000 попыток: если бы потоки теряли увеличения счётчика, блокировки бы не было.
    assert throttle.is_locked("trener")


def test_many_logins_do_not_pile_up(clock):
    throttle = auth.LoginThrottle(clock=clock)
    for i in range(1001):
        throttle.failed(f"guess{i}")
    clock.advance(auth.LOCK_SECONDS)
    throttle.failed("trener")
    assert len(throttle._entries) == 1
