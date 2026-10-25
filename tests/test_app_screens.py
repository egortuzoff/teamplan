"""Экраны приложения целиком (Streamlit AppTest): главный экран не ломается
на пустой базе и на тестовых данных, данные пользователей не попадают в HTML
без экранирования (FR-49 – FR-51, НТ-7)."""
from datetime import date, time, timedelta
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

import seed  # noqa: E402
from teamplan import auth, events, players  # noqa: E402
from teamplan.db import connect  # noqa: E402

APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture
def db_url(database_url):
    """Пустая база тестов (conftest.py): приложение подключается к ней по DATABASE_URL."""
    return database_url


def _run_as(login, password):
    conn = connect()
    try:
        actor = auth.login(conn, login, password)
    finally:
        conn.close()
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["actor"] = actor
    return at.run()


def _texts(at):
    """Весь текст экрана: Markdown, подписи, сообщения и HTML-блоки."""
    parts = [e.value for e in at.markdown] + [e.value for e in at.caption]
    parts += [e.value for e in at.info] + [e.value for e in at.error]
    parts += [str(e.proto.body) for e in at.get("html")]
    return "\n".join(str(p) for p in parts)


def _assert_no_failure(at):
    assert not at.exception, at.exception
    assert not [e.value for e in at.error], [e.value for e in at.error]


def test_empty_database_organizer(db_url):
    conn = connect()
    auth.create_organizer(conn, "boss", "boss-pass")
    conn.close()
    at = _run_as("boss", "boss-pass")
    _assert_no_failure(at)
    text = _texts(at)
    assert "Ближайших событий нет." in text
    assert "Событий нет." in text
    labels = [b.label for b in at.button]
    assert {"+ Тренировка", "+ Игра", "+ Мероприятие"} <= set(labels)


def test_empty_database_player(db_url):
    conn = connect()
    auth.create_organizer(conn, "boss", "boss-pass")
    org = auth.login(conn, "boss", "boss-pass")
    players.create_player_account(conn, org, "Тестовый Игрок", "pl", "pl-pass")
    conn.close()
    at = _run_as("pl", "pl-pass")
    _assert_no_failure(at)
    text = _texts(at)
    assert "Ближайших событий нет." in text
    assert "Последний матч" not in text  # нет сыгранных игр — блока нет
    assert "статистика" in text.lower()


def test_seed_home_screens(db_url, monkeypatch):
    monkeypatch.setattr("sys.argv", ["seed.py"])
    seed.main()
    at = _run_as("igrok1", "igrok1-demo")
    _assert_no_failure(at)
    text = _texts(at)
    assert "2:1" in text and "Победа" in text
    assert "Вы в составе" in text
    labels = [b.label for b in at.button]
    assert "✓ Буду" in labels and "Под вопросом" in labels

    at = _run_as("trener", "trener-demo-1")
    _assert_no_failure(at)
    text = _texts(at)
    assert "Требует действия" in text and "Тестовые медведи" in text
    assert "Счёт не внесён" in text and "Тестовая звезда" in text
    assert "не ответили" in text
    labels = [b.label for b in at.button]
    assert {"Опубликовать состав", "Изменить состав", "Внести счёт"} <= set(labels)


def test_attendance_button_on_home_saves_mark(db_url, monkeypatch):
    monkeypatch.setattr("sys.argv", ["seed.py"])
    seed.main()
    at = _run_as("igrok1", "igrok1-demo")
    next(b for b in at.button if b.label == "Под вопросом").click().run()
    _assert_no_failure(at)
    assert any(b.label == "✓ Под вопросом" for b in at.button)
    conn = connect()
    try:
        first = auth.login(conn, "igrok1", "igrok1-demo")
        training = next(e for e in events.list_events(conn, first) if e["type"] == "training")
        row = conn.execute(
            "SELECT status FROM attendance WHERE event_id = %s AND player_id = %s",
            (training["id"], first.player_id),
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "maybe"


def test_user_data_escaped_on_screen(db_url):
    conn = connect()
    auth.create_organizer(conn, "boss", "boss-pass")
    org = auth.login(conn, "boss", "boss-pass")
    players.create_player_account(conn, org, "<script>alert(1)</script>", "pl", "pl-pass")
    events.create_event(
        conn, org, "training", '<img src=x onerror="alert(2)">',
        date.today() + timedelta(days=1), time(23, 0), "<b>Зал</b>",
    )
    conn.close()
    for login, password in (("boss", "boss-pass"), ("pl", "pl-pass")):
        at = _run_as(login, password)
        _assert_no_failure(at)
        html = "\n".join(str(e.proto.body) for e in at.get("html"))
        assert "&lt;img src=x onerror=&quot;alert(2)&quot;&gt;" in html
        assert "<img" not in html and "<b>Зал" not in html
        assert "<script>alert(1)" not in html
