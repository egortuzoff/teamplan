"""Оформление «Табло» (НТ-7): цвета, проверка контраста и HTML-блоки главного экрана.

Все данные пользователей (имена, названия событий, соперники, места) попадают
в HTML только через esc(), поэтому вставить свою разметку или скрипт нельзя.
Основные цвета темы заданы в .streamlit/config.toml, здесь — те же значения
и дополнительные цвета для своих блоков.
"""
from datetime import datetime
from html import escape

from . import attendance
from .common import DATETIME_FMT
from .home import days_until_text, plural
from .settings import TEAM_NAME

# Цвета темы (как в .streamlit/config.toml).
BG = "#0A1428"
BG2 = "#101E3A"
TEXT = "#EAF1FB"
PRIMARY = "#7CC4FF"
BORDER = "#1E2C4A"
# Дополнительные цвета.
MUTED = "#B7C6DE"          # второстепенный текст
DIM = "#9AAACB"            # отменённые события в таблицах, подписи
CHIP = "#17294D"           # фон плашек
BUTTON_BORDER = "#2D3F63"  # рамка кнопок и плашек
AMBER = "#FFB020"          # «требует действия»: рамки, значки, счётчик
AMBER_TEXT = "#FFD27A"     # янтарный текст на тёмном фоне
AMBER_BG = "#3A2A0B"
GREEN_BG = "#12422E"       # «Буду», «Вы в составе», «Победа»
GREEN_TEXT = "#8BF0B7"
GREEN_BORDER = "#3DDC84"

# Пары «текст — фон», которые встречаются на экране; контраст не ниже 4.5:1 (НТ-7).
TEXT_PAIRS = [
    (TEXT, BG), (TEXT, BG2), (TEXT, CHIP),
    (PRIMARY, BG), (PRIMARY, BG2),
    (BG, PRIMARY),  # текст основной кнопки
    (MUTED, BG), (MUTED, BG2), (MUTED, CHIP),
    (DIM, BG), (DIM, BG2),
    (AMBER_TEXT, BG), (AMBER_TEXT, BG2), (AMBER_TEXT, AMBER_BG),
    (AMBER, BG2),  # подписи «Требует действия», «Счёт не внесён»
    (BG, AMBER),   # число непрочитанных на янтарном
    (GREEN_TEXT, GREEN_BG), (GREEN_TEXT, BG2),
]

WEEKDAYS = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]
TYPE_LABELS_UPPER = {"training": "ТРЕНИРОВКА", "game": "ИГРА", "activity": "МЕРОПРИЯТИЕ"}
MONO = "'JetBrains Mono', monospace"
HEAD = "'Oswald', sans-serif"


def _luminance(color):
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(fg, bg):
    """Контраст по WCAG 2.x между цветами вида #RRGGBB."""
    a, b = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def esc(value):
    """Экранирует пользовательский текст для HTML."""
    return escape("" if value is None else str(value), quote=True)


def short_date(event):
    """«ПТ 17.10 · 19:30»."""
    day = event["start"]
    return f"{WEEKDAYS[day.weekday()]} {day:%d.%m} · {event['time']}"


def short_datetime(value):
    """«15.10, 18:00» из ГГГГ-ММ-ДД ЧЧ:ММ."""
    return f"{datetime.strptime(value, DATETIME_FMT):%d.%m, %H:%M}"


def _label(text, color=PRIMARY):
    return (
        f'<div style="font-family:{MONO};font-size:13px;letter-spacing:.12em;'
        f'color:{color};text-transform:uppercase">{text}</div>'
    )


def _chip(text, green=False):
    colors = (
        f"background:{GREEN_BG};color:{GREEN_TEXT};font-weight:600"
        if green else f"background:{CHIP};color:{TEXT}"
    )
    return f'<span style="padding:6px 12px;border-radius:6px;font-size:14px;{colors}">{text}</span>'


def _chips(items):
    return (
        '<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">'
        + "".join(items) + "</div>"
    )


def _big_type(event_type, date_text):
    return (
        '<div style="display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 20px">'
        f'<div style="font-family:{HEAD};font-weight:700;font-size:clamp(34px,15cqi,88px);'
        f'line-height:.95;text-transform:uppercase;overflow-wrap:anywhere">{TYPE_LABELS_UPPER[event_type]}</div>'
        f'<div style="font-family:{HEAD};font-weight:500;font-size:clamp(22px,7cqi,38px);'
        f'line-height:1.05;color:{MUTED}">{date_text}</div></div>'
    )


def _block(*parts, gap=16):
    # container-type: размеры крупных надписей считаются от ширины карточки (cqi).
    return (
        f'<div style="display:flex;flex-direction:column;gap:{gap}px;container-type:inline-size">'
        + "".join(parts) + "</div>"
    )


def _cancelled_mark(event):
    if event["status"] != "cancelled":
        return ""
    return _chip("🚫 Отменено")


def _place_line(event, with_title=True):
    """Название (если не совпадает с типом), соперник, место, время сбора."""
    parts = []
    if event["type"] == "game" and event.get("opponent"):
        parts.append(f"«{esc(event['opponent'])}»")
    elif with_title and event["title"] != event["type_label"]:
        parts.append(esc(event["title"]))
    if event.get("place"):
        parts.append(esc(event["place"]))
    if event.get("gather_time"):
        parts.append(f"сбор {esc(event['gather_time'])}")
    return " · ".join(parts)


def _squad_note(event):
    """Пометка о составе для игры глазами игрока (FR-25, FR-49)."""
    if event.get("in_squad"):
        return _chip("✓ Вы в составе", green=True)
    if event.get("squad_published"):
        return _chip("Состав опубликован, вас в нём нет")
    return _chip("Состав пока не объявлен")


# ---------- игрок (FR-49) ----------

def next_event_html(ev):
    """Блок «Следующее событие»."""
    head = _label(f"Следующее событие · {esc(days_until_text(ev['days_until']))}")
    if ev["type"] == "game":
        rival = esc(ev["opponent"]) if ev.get("opponent") else None
        line = f"против <b>«{rival}»</b>" if rival else esc(ev["title"])
    elif ev["title"] != ev["type_label"]:
        line = esc(ev["title"])
    else:
        line = ""
    chips = []
    if ev.get("gather_time"):
        chips.append(_chip(f"Сбор {esc(ev['gather_time'])}"))
    chips.append(_chip(esc(ev["place"]) if ev.get("place") else "Место не указано"))
    if ev["type"] == "game":
        chips.append(_squad_note(ev))
    parts = [head, _big_type(ev["type"], esc(short_date(ev)))]
    if line:
        parts.append(f'<div style="font-size:20px">{line}</div>')
    return _block(*parts, _chips(chips), gap=18)


def outcome_chip(outcome):
    green = outcome == "Победа"
    colors = f"background:{GREEN_BG};color:{GREEN_TEXT}" if green else f"background:{CHIP};color:{TEXT}"
    return (
        f'<span style="padding:4px 12px;border-radius:999px;font-size:13px;'
        f'font-weight:600;{colors}">{esc(outcome)}</span>'
    )


def last_match_html(m):
    """Блок «Последний матч»: табло со счётом, итог, дата, авторы голов (FR-47)."""
    head = (
        '<div style="display:flex;justify-content:space-between;align-items:center;gap:12px">'
        + _label(f"Последний матч · {m['start']:%d.%m}") + outcome_chip(m["outcome"]) + "</div>"
    )
    team = (
        f'font-family:{HEAD};font-weight:600;font-size:clamp(14px,4.5cqi,22px);'
        'text-transform:uppercase;line-height:1.15;overflow-wrap:anywhere'
    )
    board = (
        '<div style="display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);'
        'align-items:center;gap:12px;text-align:center">'
        f'<div style="{team}">{esc(TEAM_NAME)}</div>'
        f'<div style="font-family:{MONO};font-weight:700;font-size:clamp(40px,15cqi,84px);'
        f'line-height:1;color:#FFFFFF;white-space:nowrap" '
        f'aria-label="Счёт {m["our_score"]} : {m["their_score"]}">'
        f'{m["our_score"]}:{m["their_score"]}</div>'
        f'<div style="{team};color:{MUTED}">{esc(m.get("opponent") or "Соперник")}</div></div>'
    )
    if m["goals"]:
        goals = ", ".join(
            f'<b style="color:#FFFFFF">{esc(g["scorer_name"])}</b>'
            + (f" ({esc(g['assist_name'])})" if g.get("assist_name") else "")
            for g in m["goals"]
        )
        goals = f"Голы: {goals}"
    else:
        goals = "Голы не указаны."
    foot = (
        f'<div style="font-size:15px;color:{MUTED};border-top:1px solid {BORDER};'
        f'padding-top:14px">{goals}</div>'
    )
    return _block(head, board, foot, gap=22)


def week_card_html(ev):
    """Карточка события недели (без кнопок явки — они отдельными виджетами)."""
    icon = {"training": "⛸️", "game": "🏒", "activity": "👥"}[ev["type"]]
    parts = [
        _label(f"{icon} {TYPE_LABELS_UPPER[ev['type']]} · {esc(days_until_text(ev['days_until']))}"),
        f'<div style="font-family:{HEAD};font-weight:600;font-size:32px;line-height:1">'
        f"{esc(short_date(ev))}</div>",
    ]
    details = _place_line(ev)
    if ev["type"] != "game" and ev.get("reply_by"):
        details += (" · " if details else "") + f"ответить до {esc(short_datetime(ev['reply_by']))}"
    if details:
        parts.append(f'<div style="font-size:15px;color:{MUTED}">{details}</div>')
    if ev["status"] == "cancelled":
        parts.append(_chips([_cancelled_mark(ev)]))
    elif ev["type"] == "game":
        parts.append(_chips([_squad_note(ev)]))
    elif ev.get("my_mark"):
        label = attendance.ATTENDANCE_LABELS[ev["my_mark"]]
        parts.append(f'<div style="font-size:14px;color:{MUTED}">Ваш ответ: <b style="color:{TEXT}">{label}</b></div>')
    else:
        parts.append(f'<div style="font-size:14px;color:{AMBER_TEXT}">Вы ещё не ответили</div>')
    return _block(*parts, gap=12)


def stats_html(stats):
    """Блок «Моя статистика» (FR-32, FR-33)."""
    cells = [
        (stats["games"], plural(stats["games"], ("игра", "игры", "игр")), TEXT),
        (stats["goals"], plural(stats["goals"], ("гол", "гола", "голов")), PRIMARY),
        (stats["assists"], plural(stats["assists"], ("передача", "передачи", "передач")), TEXT),
    ]
    items = "".join(
        f'<div><div style="font-family:{MONO};font-weight:700;font-size:52px;line-height:1;'
        f'color:{color}">{value}</div><div style="color:{MUTED};font-size:15px">{label}</div></div>'
        for value, label, color in cells
    )
    return (
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,160px),1fr));'
        'gap:16px;align-items:end">'
        f'<div style="font-family:{HEAD};font-weight:600;font-size:28px;letter-spacing:.04em;'
        'text-transform:uppercase;line-height:1.1">Моя<br>статистика</div>'
        + items + "</div>"
    )


# ---------- организатор (FR-50) ----------

def needs_action_html(ev):
    """Блок «Требует действия»: ближайшая игра с неопубликованным составом."""
    reason = "состав не опубликован" if ev["squad_players"] else "состав не сформирован"
    rival = f"против <b>«{esc(ev['opponent'])}»</b>" if ev.get("opponent") else esc(ev["title"])
    rest = []
    if ev.get("place"):
        rest.append(esc(ev["place"]))
    if ev.get("gather_time"):
        rest.append(f"сбор {esc(ev['gather_time'])}")
    line = " · ".join([rival] + rest)
    if ev["squad_players"]:
        chips = [f'<span style="font-size:15px;color:{MUTED}">Черновик состава:</span>']
        for p in ev["squad_players"]:
            number = f" №{p['number']}" if p["number"] is not None else ""
            chips.append(_chip(esc(p["name"]) + number))
        chips.append(f'<span style="font-size:14px;color:{MUTED}">игроки пока не видят</span>')
    else:
        chips = [f'<span style="font-size:15px;color:{MUTED}">Игроки в состав ещё не выбраны.</span>']
    return _block(
        _label(f"⚠ Требует действия · {reason} · {esc(days_until_text(ev['days_until']))}", AMBER),
        _big_type("game", esc(short_date(ev))),
        f'<div style="font-size:20px">{line}</div>', _chips(chips), gap=18,
    )


def no_score_html(ev):
    """Строка блока «Счёт не внесён»."""
    return _block(
        _label("⚠ Счёт не внесён", AMBER),
        f'<div style="font-family:{HEAD};font-weight:600;font-size:26px;line-height:1.2">'
        f"{esc(short_date(ev))} · {esc(ev['title'])}</div>",
        gap=4,
    )


ATTENDANCE_CELLS = [
    ("yes", "буду", GREEN_BG, GREEN_TEXT),
    ("maybe", "под вопросом", CHIP, TEXT),
    ("no", "не буду", CHIP, TEXT),
    ("none", "не ответили", AMBER_BG, AMBER_TEXT),
]


def attendance_card_html(ev):
    """Карточка блока «Явка на неделе» со сводкой и списком не ответивших."""
    cells = "".join(
        f'<div style="padding:10px 4px;border-radius:8px;background:{bg}">'
        f'<div style="font-family:{MONO};font-weight:700;font-size:26px;color:{fg}">'
        f'{ev["counts"][key]}</div><div style="font-size:12px;color:{fg}">{label}</div></div>'
        for key, label, bg, fg in ATTENDANCE_CELLS
    )
    parts = [
        _label(f"{TYPE_LABELS_UPPER[ev['type']]} · {esc(days_until_text(ev['days_until']))}"),
        f'<div style="font-family:{HEAD};font-weight:600;font-size:32px;line-height:1">'
        f"{esc(short_date(ev))}</div>",
    ]
    details = _place_line(ev)
    if details:
        parts.append(f'<div style="font-size:15px;color:{MUTED}">{details}</div>')
    parts.append(
        '<div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px;'
        f'text-align:center">{cells}</div>'
    )
    deadline = f" · срок {esc(short_datetime(ev['reply_by']))}" if ev.get("reply_by") else ""
    if ev["missing"]:
        names = ", ".join(esc(n) for n in ev["missing"])
        if ev["highlight_missing"]:
            text = f'<b style="color:{AMBER_TEXT}">Срок ответа прошёл, не ответили:</b> {names}'
        else:
            text = f"Не ответили: {names}{deadline}"
    else:
        text = f"Ответили все{deadline}"
    parts.append(f'<div style="font-size:14px;color:{MUTED}">{text}</div>')
    return _block(*parts, gap=12)


# ---------- общее ----------

def section_title_html(text):
    return (
        f'<h2 style="margin:0;font-family:{HEAD};font-weight:600;font-size:28px;'
        f'letter-spacing:.04em;text-transform:uppercase;color:{TEXT}">{esc(text)}</h2>'
    )


def label_html(text, amber=False):
    return _label(esc(text), AMBER if amber else PRIMARY)


def note_html(text):
    return f'<div style="font-size:15px;color:{MUTED}">{esc(text)}</div>'


def user_html(name, subtitle):
    """Имя пользователя и подпись справа в шапке."""
    return (
        '<div style="text-align:right;line-height:1.3">'
        f'<div style="font-weight:600;color:{TEXT}">{esc(name)}</div>'
        f'<div style="font-size:13px;color:{MUTED}">{esc(subtitle)}</div></div>'
    )


def brand_html():
    return (
        f'<div style="font-family:{HEAD};font-weight:600;font-size:20px;letter-spacing:.06em;'
        f'text-transform:uppercase;color:{TEXT}">{esc(TEAM_NAME)}</div>'
    )
