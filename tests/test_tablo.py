"""Оформление «Табло» (НТ-7): контраст цветов, шрифты темы, экранирование HTML."""
import tomllib
from datetime import time, timedelta
from pathlib import Path

import pytest

from teamplan import attendance, auth, events, home, players, results, squads, tablo

CONFIG = Path(__file__).resolve().parent.parent / ".streamlit" / "config.toml"


def test_contrast_formula():
    assert tablo.contrast("#000000", "#FFFFFF") == pytest.approx(21)
    assert tablo.contrast("#FFFFFF", "#000000") == pytest.approx(21)
    assert tablo.contrast("#7CC4FF", "#7CC4FF") == pytest.approx(1)


@pytest.mark.parametrize("fg, bg", tablo.TEXT_PAIRS)
def test_text_pairs_contrast_at_least_4_5(fg, bg):
    assert tablo.contrast(fg, bg) >= 4.5


def test_theme_config_matches_tablo():
    theme = tomllib.loads(CONFIG.read_text(encoding="utf-8"))["theme"]
    assert theme["base"] == "dark"
    assert theme["backgroundColor"] == tablo.BG
    assert theme["secondaryBackgroundColor"] == tablo.BG2
    assert theme["textColor"] == tablo.TEXT
    assert theme["primaryColor"] == tablo.PRIMARY
    assert theme["borderColor"] == tablo.BORDER
    for bg in (theme["backgroundColor"], theme["secondaryBackgroundColor"]):
        assert tablo.contrast(theme["textColor"], bg) >= 4.5
        assert tablo.contrast(theme["primaryColor"], bg) >= 4.5
    assert tablo.contrast(theme["greenTextColor"], theme["greenBackgroundColor"]) >= 4.5
    assert tablo.contrast(theme["orangeTextColor"], theme["backgroundColor"]) >= 4.5


def test_theme_fonts_from_google_fonts():
    theme = tomllib.loads(CONFIG.read_text(encoding="utf-8"))["theme"]
    for key, family in (("font", "Golos+Text"), ("headingFont", "Oswald"),
                        ("codeFont", "JetBrains+Mono")):
        name, url = theme[key].split(":", 1)
        assert family.replace("+", " ") in name
        assert url.startswith("https://fonts.googleapis.com/css2?family=" + family)


def test_esc():
    assert tablo.esc('<b a="1">&') == "&lt;b a=&quot;1&quot;&gt;&amp;"
    assert tablo.esc(None) == ""


EVIL_NAME = "<script>alert('name')</script>"
EVIL_TITLE = '<img src=x onerror="alert(1)">'
EVIL_PLACE = '"><svg onload=alert(2)>'
EVIL_OPPONENT = "</div><script>alert(3)</script>"


@pytest.fixture
def evil_data(conn, org, now):
    """Данные, в которых пользователи пытаются вставить свою разметку и скрипты."""
    pid = players.create_player_account(conn, org, EVIL_NAME, "evil", "pass-evil", 9, "<i>поз</i>")
    other = players.create_player_account(conn, org, EVIL_TITLE, "evil2", "pass-evil2")
    player = auth.login(conn, "evil", "pass-evil")
    week_ago = now - timedelta(days=7)
    played = events.create_event(
        conn, org, "game", EVIL_TITLE, now.date() - timedelta(days=2), time(19, 0),
        EVIL_PLACE, now=week_ago, opponent=EVIL_OPPONENT,
    )
    squads.save_squad(conn, org, played, [pid, other], now=week_ago)
    squads.publish_squad(conn, org, played, now=week_ago)
    results.save_result(conn, org, played, 1, 0, [(pid, other)], now=now)
    unscored = events.create_event(
        conn, org, "game", EVIL_TITLE, now.date() - timedelta(days=1), time(19, 0),
        EVIL_PLACE, now=week_ago, opponent=EVIL_OPPONENT,
    )
    squads.save_squad(conn, org, unscored, [pid], now=week_ago)
    squads.publish_squad(conn, org, unscored, now=week_ago)
    reply_by = (now + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    for event_type in ("training", "activity"):
        events.create_event(
            conn, org, event_type, EVIL_TITLE, now.date() + timedelta(days=2), time(19, 0),
            EVIL_PLACE, reply_by=reply_by if event_type == "training" else None,
            gather_time=time(18, 30), now=now,
        )
    future = events.create_event(
        conn, org, "game", EVIL_TITLE, now.date() + timedelta(days=3), time(19, 0),
        EVIL_PLACE, now=now, opponent=EVIL_OPPONENT,
    )
    squads.save_squad(conn, org, future, [pid, other], now=now)
    first_training = events.list_events(conn, player, now)[2]["id"]
    attendance.set_attendance(conn, player, first_training, pid, "yes", now=now)
    return player


def _all_blocks(conn, org, player, now):
    p = home.player_home(conn, player, now=now)
    o = home.organizer_home(conn, org, now=now)
    blocks = [
        tablo.next_event_html(p["next_event"]),
        tablo.last_match_html(p["last_match"]),
        tablo.stats_html(p["stats"]),
        tablo.needs_action_html(o["needs_action"]),
        tablo.user_html(player.name, "<b>подпись</b>"),
        tablo.section_title_html("<h1>"),
        tablo.label_html("<u>"),
        tablo.note_html("<i>"),
        tablo.brand_html(),
    ]
    blocks += [tablo.week_card_html(e) for e in p["week"]]
    blocks += [tablo.no_score_html(e) for e in o["no_score"]]
    blocks += [tablo.attendance_card_html(e) for e in o["week_attendance"]]
    return p, o, blocks


def test_user_data_is_escaped_in_html(conn, org, now, evil_data):
    p, o, blocks = _all_blocks(conn, org, evil_data, now)
    # Все блоки построены на «опасных» данных.
    assert p["next_event"] and p["last_match"] and o["needs_action"]
    assert o["no_score"] and o["week_attendance"] and len(p["week"]) == 3
    html = "\n".join(blocks)
    for raw in ("<script", "<img", "<svg", "</div><script", "<i>", "<b>подпись", "<h1>", "<u>"):
        assert raw not in html, raw
    assert "&lt;script&gt;alert(&#x27;name&#x27;)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in html
    assert "&quot;&gt;&lt;svg onload=alert(2)&gt;" in html
    assert "&lt;/div&gt;&lt;script&gt;alert(3)&lt;/script&gt;" in html


def test_each_block_escapes_on_its_own(conn, org, now, evil_data):
    _, _, blocks = _all_blocks(conn, org, evil_data, now)
    for block in blocks:
        assert "<script" not in block
        assert "onerror=\"" not in block
        # Теги открываются и закрываются парами — данные не ломают разметку.
        assert block.count("<div") == block.count("</div>")
