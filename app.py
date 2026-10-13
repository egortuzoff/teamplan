"""TeamPlan: расписание, явка, составы и результаты команды (ТЗ TP-TZ-001 версии 0.6).

Запуск: streamlit run app.py
Вся логика и проверка прав находятся в пакете teamplan; здесь только экраны.
"""
import logging
from datetime import datetime, time
from pathlib import Path

import pandas as pd
import streamlit as st

from teamplan import (
    attendance, auth, events, notifications, players, results, squads, summary,
)
from teamplan.common import DATE_FMT, DATETIME_FMT, TIME_FMT, format_date, format_datetime
from teamplan.db import connect
from teamplan.errors import ServiceError
from teamplan.settings import TEAM_NAME

logger = logging.getLogger("teamplan")

PAGE_SCHEDULE = "Расписание"
PAGE_STATS = "Статистика"
PAGE_NOTIFICATIONS = "Уведомления"
PAGE_NEW_EVENT = "Новое событие"
PAGE_PLAYERS = "Игроки"

TYPE_OPTIONS = list(events.EVENT_TYPES)

# Оформление в цветах команды. Сами цвета темы — в .streamlit/config.toml,
# название команды — в teamplan/settings.py.
PAGE_TITLE = TEAM_NAME.replace("«", "").replace("»", "") + " — TeamPlan"
LOGO = Path(__file__).parent / "assets" / "logo.png"
TYPE_ICONS = {"training": "⛸️", "game": "🏒", "activity": "👥"}
CANCELLED_MARK = "🚫 Отменено"
# Статус для показа (FR-48): значок в таблицах и цвет значка в карточках.
STATUS_ICONS = {
    "planned": "🕒", "past": "☑️", "played": "🏁", "no_score": "⚠️", "cancelled": "🚫",
}
STATUS_COLORS = {
    "planned": "green", "past": "gray", "played": "violet", "no_score": "orange",
    "cancelled": "red",
}
CANCELLED_TEXT_COLOR = "#5B6275"  # серый, контраст на белом 6.1:1
# Цвета статусов явки: «буду» — зелёный, «под вопросом» — жёлтый, «не буду» — красный.
ATTENDANCE_COLORS = {"yes": "green", "maybe": "yellow", "no": "red", "none": "gray"}
ATTENDANCE_DOTS = {"yes": "🟢", "maybe": "🟡", "no": "🔴", "none": "⚪"}
NO_ANSWER_LABEL = "Не ответили"
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
UPCOMING_CARDS = 4
MD_SPECIAL = set("\\`*_{}[]<>()#+-.!|~:$")


# ---------- вспомогательные функции ----------

def flash(kind, text):
    """Сообщение, которое будет показано после перезагрузки страницы."""
    st.session_state["flash"] = (kind, text)


def show_flash():
    item = st.session_state.pop("flash", None)
    if item:
        kind, text = item
        if kind == "success":
            st.success(text)
        elif kind == "warning":
            st.warning(text)
        else:
            st.error(text)


def perform(action, success_text):
    """Выполняет действие. При ошибке показывает причину, при успехе обновляет страницу."""
    try:
        action()
    except ServiceError as exc:
        st.error(str(exc))
        return
    flash("success", success_text)
    st.rerun()


def to_date(value):
    return datetime.strptime(value, DATE_FMT).date()


def to_time(value):
    return datetime.strptime(value, TIME_FMT).time()


def md(text):
    """Экранирует Markdown в пользовательском тексте (названия, места)."""
    return "".join("\\" + ch if ch in MD_SPECIAL else ch for ch in text)


def type_text(ev):
    return f"{TYPE_ICONS[ev['type']]} {ev['type_label']}"


def status_text(ev):
    """Статус для показа со значком (FR-48)."""
    return f"{STATUS_ICONS[ev['display_status']]} {ev['display_status_label']}"


def event_badges(ev, compact=False):
    """Значки типа и статуса события в виде Markdown.

    compact — статус без значка, чтобы он помещался в узкой карточке.
    """
    type_color = "gray" if ev["status"] == "cancelled" else "blue"
    status_color = STATUS_COLORS[ev["display_status"]]
    status = ev["display_status_label"] if compact else status_text(ev)
    return f":{type_color}-badge[{type_text(ev)}] :{status_color}-badge[{status}]"


def attendance_text(key):
    label = NO_ANSWER_LABEL if key == "none" else attendance.ATTENDANCE_LABELS[key]
    return f"{ATTENDANCE_DOTS[key]} {label}"


def event_label(ev):
    suffix = f" · {CANCELLED_MARK}" if ev["status"] == "cancelled" else ""
    return (
        f"{format_date(ev['date'])} {ev['time']} · {TYPE_ICONS[ev['type']]} "
        f"{ev['type_label']} · {ev['title']}{suffix}"
    )


def player_label(p):
    return p["name"] + (f" (№{p['number']})" if p["number"] is not None else "")


# ---------- вход ----------

def show_login(conn):
    _, middle, _ = st.columns([1, 2, 1])
    with middle:
        with st.container(horizontal_alignment="center"):
            st.image(str(LOGO), width=160)
        st.title(TEAM_NAME, text_alignment="center")
        st.markdown(
            "Добро пожаловать в **TeamPlan**! Здесь расписание тренировок и игр, "
            "отметки явки, составы и результаты команды.",
            text_alignment="center",
        )
        with st.form("login_form"):
            login_name = st.text_input("Логин")
            password = st.text_input("Пароль", type="password")
            submitted = st.form_submit_button("Войти", type="primary", width="stretch")
    if not submitted:
        return
    with middle:
        try:
            actor = auth.login(conn, login_name, password)
        except ServiceError as exc:
            st.error(str(exc))
            return
        st.session_state["actor"] = actor
        st.session_state["just_logged_in"] = True
        st.rerun()


def show_sidebar(conn, actor):
    st.logo(str(LOGO), size="large")
    st.sidebar.markdown(f"### {TEAM_NAME}")
    st.sidebar.caption("TeamPlan — расписание команды")
    st.sidebar.divider()
    st.sidebar.markdown(f"**{actor.name}**")
    st.sidebar.caption(auth.ROLE_LABELS[actor.role])
    pages = [PAGE_SCHEDULE, PAGE_STATS, PAGE_NOTIFICATIONS]
    if actor.is_organizer:
        pages += [PAGE_NEW_EVENT, PAGE_PLAYERS]
    unread = notifications.unread_count(conn, actor)
    # Переход на другую страницу из кода: значение задаём до создания переключателя.
    if "goto_page" in st.session_state:
        st.session_state["page"] = st.session_state.pop("goto_page")
    if st.session_state.get("page") not in pages:
        st.session_state["page"] = pages[0]
    page = st.sidebar.radio(
        "Раздел", pages, key="page",
        format_func=lambda p: f"{p} ({unread})" if p == PAGE_NOTIFICATIONS and unread else p,
    )
    if st.sidebar.button("Выйти"):
        st.session_state.clear()
        st.rerun()
    return page


# ---------- расписание и карточка события ----------

def show_schedule(conn, actor):
    st.header("Расписание")
    evs = events.list_events(conn, actor)
    if not evs:
        hint = " Создайте первое в разделе «Новое событие»." if actor.is_organizer else ""
        st.info("Событий нет." + hint)
        return
    show_upcoming(evs)
    ids = [e["id"] for e in evs]
    by_id = {e["id"]: e for e in evs}
    # Вкладки по типам событий (FR-43); выбор строки открывает карточку (FR-46).
    # Ключ сохраняет выбранную вкладку при перезапуске страницы.
    tab_all, tab_games, tab_trainings, tab_activities = st.tabs(
        ["Все", "Игры", "Тренировки", "Мероприятия"], key="schedule_tab", on_change="rerun",
    )
    with tab_all:
        schedule_table("all", evs, [
            {
                "Дата": format_date(e["date"]),
                "Время": e["time"],
                "Время сбора": e["gather_time"] or "",
                "Тип": type_text(e),
                "Название": e["title"],
                "Место": e["place"] or "",
                "Статус": status_text(e),
            }
            for e in evs
        ])
    with tab_games:
        games = summary.games_table(conn, actor)
        schedule_table("game", games, [
            {
                **event_columns(g, opponent=True),
                "Счёт": g["score"],
                "Итог": g["outcome"],
                "Статус": status_text(g),
            }
            for g in games
        ])
    for tab, event_type in ((tab_trainings, "training"), (tab_activities, "activity")):
        with tab:
            items = summary.attendance_table(conn, actor, event_type)
            schedule_table(event_type, items, [
                {
                    **event_columns(e),
                    **{
                        attendance_text(key): e["counts"][key]
                        for key in ("yes", "maybe", "no", "none")
                    },
                    "Статус": status_text(e),
                }
                for e in items
            ])
    if "goto_event" in st.session_state:
        st.session_state["event_select"] = st.session_state.pop("goto_event")
    if st.session_state.get("event_select") not in ids:
        now = datetime.now()
        upcoming = [e for e in evs if e["start"] >= now]
        st.session_state["event_select"] = (upcoming[0] if upcoming else evs[-1])["id"]
    event_id = st.selectbox(
        "Открыть событие", ids, key="event_select",
        format_func=lambda i: event_label(by_id[i]),
    )
    show_event_card(conn, actor, by_id[event_id])


def event_columns(ev, opponent=False):
    """Общие колонки таблиц игр, тренировок и мероприятий (FR-44, FR-45).

    opponent — колонка «Соперник» после названия (только в таблице игр).
    """
    columns = {
        "Дата": format_date(ev["date"]),
        "Время": ev["time"],
        "Время сбора": ev["gather_time"] or "",
        "Название": ev["title"],
    }
    if opponent:
        columns["Соперник"] = ev["opponent"] or ""
    columns["Место"] = ev["place"] or ""
    return columns


def schedule_table(name, evs, rows):
    """Таблица вкладки расписания с выбором строки (FR-46)."""
    if not rows:
        st.info("Событий этого типа нет.")
        return
    table = pd.DataFrame(rows)
    cancelled = [e["status"] == "cancelled" for e in evs]
    # Отменённые события в таблице — серым текстом.
    styled = table.style.apply(
        lambda row: [
            f"color: {CANCELLED_TEXT_COLOR}" if cancelled[row.name] else ""
        ] * len(row),
        axis=1,
    )
    key = f"schedule_table_{name}"
    ids = [e["id"] for e in evs]
    st.dataframe(
        styled, hide_index=True, key=key,
        on_select=lambda: select_row(key, ids), selection_mode="single-row",
        column_config={
            **{
                col: st.column_config.TextColumn(width="small")
                for col in ("Дата", "Время", "Время сбора", "Счёт", "Итог")
            },
            **{
                attendance_text(key): st.column_config.NumberColumn(width="small")
                for key in ("yes", "maybe", "no", "none")
            },
            "Название": st.column_config.TextColumn(width="medium"),
        },
    )
    st.caption("Выберите строку, чтобы открыть карточку события.")


def select_row(key, ids):
    """Открывает событие выбранной строки (вызывается до отрисовки списка событий)."""
    rows = st.session_state[key].selection.rows
    if rows:
        open_event(ids[rows[0]])


def open_event(event_id):
    """Выбирает событие в списке «Открыть событие» (вызывается до отрисовки списка)."""
    st.session_state["event_select"] = event_id


def show_upcoming(evs):
    """Карточки ближайших будущих событий над таблицей расписания."""
    now = datetime.now()
    upcoming = [e for e in evs if e["start"] >= now][:UPCOMING_CARDS]
    if not upcoming:
        return
    st.subheader("Ближайшие события")
    cols = st.columns(UPCOMING_CARDS)
    for col, ev in zip(cols, upcoming):
        cancelled = ev["status"] == "cancelled"
        with col.container(border=True, height="stretch"):
            st.markdown(event_badges(ev, compact=True))
            title = md(ev["title"])
            st.markdown(f":gray[~~{title}~~]" if cancelled else f"**{title}**")
            day = to_date(ev["date"])
            lines = [
                f"📅 {format_date(ev['date'])}, {WEEKDAYS[day.weekday()]}",
                f"🕖 Начало: {ev['time']}",
            ]
            if ev["gather_time"]:
                lines.append(f"⏰ Сбор: {ev['gather_time']}")
            lines.append(f"📍 {md(ev['place'])}" if ev["place"] else "📍 Место не указано")
            text = "  \n".join(lines)
            st.markdown(f":gray[{text}]" if cancelled else text)
            st.button(
                "Открыть", key=f"card_open_{ev['id']}", width="stretch",
                on_click=open_event, args=(ev["id"],),
            )


def show_event_card(conn, actor, ev):
    st.divider()
    cancelled = ev["status"] == "cancelled"
    title = md(ev["title"])
    st.subheader(
        f"{TYPE_ICONS[ev['type']]} " + (f":gray[~~{title}~~]" if cancelled else title)
    )
    st.markdown(event_badges(ev))
    line = f"📅 {format_date(ev['date'])} в {ev['time']}"
    if ev["gather_time"]:
        line += f" · ⏰ сбор в {ev['gather_time']}"
    if ev["place"]:
        line += f" · 📍 {md(ev['place'])}"
    st.markdown(line)
    if cancelled:
        st.error("Событие отменено.", icon="🚫")
    if ev["type"] == "game":
        show_squad(conn, actor, ev)
        show_result(conn, actor, ev)
    else:
        show_attendance(conn, actor, ev)
    if actor.is_organizer:
        show_event_admin(conn, actor, ev)


def show_attendance(conn, actor, ev):
    st.markdown("#### Явка")
    summary = attendance.attendance_summary(conn, actor, ev["id"])
    if ev["reply_by"]:
        st.caption(f"Ответить до: {format_datetime(ev['reply_by'])}")
    counts = summary["counts"]
    cols = st.columns(4)
    for col, key in zip(cols, ("yes", "maybe", "no", "none")):
        with col.container(border=True):
            st.metric(f":{ATTENDANCE_COLORS[key]}[{attendance_text(key)}]", counts[key])

    missing = ", ".join(p["name"] for p in summary["by_status"]["none"])
    if summary["highlight_missing"]:
        st.error(f"Срок ответа прошёл, не ответили: {missing}")
    elif missing:
        st.info(f"Ещё не ответили: {missing}")
    for key in ("yes", "maybe", "no"):
        names = ", ".join(md(p["name"]) for p in summary["by_status"][key])
        if names:
            st.markdown(f":{ATTENDANCE_COLORS[key]}-badge[{attendance_text(key)}] {names}")

    if ev["status"] == "cancelled":
        st.caption("Событие отменено, явку отметить нельзя.")
        return
    if events.is_started(ev):
        st.caption("Событие уже началось, отметки не меняются.")
        return

    if actor.is_organizer:
        all_players = players.list_players(conn, actor)
        if not all_players:
            st.info("Сначала добавьте игроков в раздел «Игроки».")
            return
        by_id = {p["id"]: p for p in all_players}
        target = st.selectbox(
            "Отметить явку за игрока", list(by_id), key=f"att_player_{ev['id']}",
            format_func=lambda i: player_label(by_id[i]),
        )
    else:
        target = actor.player_id

    current = summary["marks"].get(target)
    options = ["yes", "maybe", "no"]
    with st.form(f"att_form_{ev['id']}_{target}"):
        choice = st.radio(
            "Моя явка" if not actor.is_organizer else "Явка",
            options,
            index=options.index(current) if current else None,
            format_func=lambda k: f":{ATTENDANCE_COLORS[k]}[{attendance_text(k)}]",
            key=f"att_choice_{ev['id']}_{target}",
        )
        submitted = st.form_submit_button("Сохранить отметку")
    if submitted:
        if choice is None:
            st.error("Выберите вариант: «Буду», «Под вопросом» или «Не буду».")
        else:
            perform(
                lambda: attendance.set_attendance(conn, actor, ev["id"], target, choice),
                "Отметка сохранена.",
            )


def squad_rows(squad_players):
    return [
        {
            "№ п/п": i,
            "Игрок": p["name"],
            "Игровой номер": "" if p["number"] is None else p["number"],
            "Позиция": p["position"] or "",
        }
        for i, p in enumerate(squad_players, start=1)
    ]


def show_squad(conn, actor, ev):
    st.markdown("#### Состав на игру")
    squad = squads.get_squad(conn, actor, ev["id"])

    if not actor.is_organizer:
        if squad["state"] == "published":
            st.dataframe(squad_rows(squad["players"]), hide_index=True)
        else:
            st.info("Состав пока не объявлен.")
        return

    st.caption(f"Статус: {squads.STATE_LABELS[squad['state']]}")
    if squad["players"]:
        st.dataframe(squad_rows(squad["players"]), hide_index=True)
    if ev["status"] == "cancelled":
        st.caption("Игра отменена, состав изменить нельзя.")
        return
    all_players = players.list_players(conn, actor)
    if not all_players:
        st.info("Сначала добавьте игроков в раздел «Игроки».")
        return
    by_id = {p["id"]: p for p in all_players}
    published = squad["state"] == "published"
    with st.form(f"squad_form_{ev['id']}"):
        chosen = st.multiselect(
            "Игроки в составе", list(by_id),
            default=[p["id"] for p in squad["players"]],
            format_func=lambda i: player_label(by_id[i]),
            key=f"squad_choice_{ev['id']}",
        )
        if published:
            st.caption("Состав опубликован: изменения видны игрокам сразу после сохранения.")
            save = st.form_submit_button("Сохранить изменения состава")
            publish = False
        else:
            save = st.form_submit_button("Сохранить черновик")
            publish = st.form_submit_button("Сохранить и опубликовать")

    if save:
        perform(lambda: squads.save_squad(conn, actor, ev["id"], chosen), "Состав сохранён.")
    if publish:
        def save_and_publish():
            squads.save_squad(conn, actor, ev["id"], chosen)
            squads.publish_squad(conn, actor, ev["id"])
        perform(save_and_publish, "Состав опубликован.")


def show_result(conn, actor, ev):
    """Результат игры: счёт, голы и передачи (FR-27 – FR-31)."""
    st.markdown("#### Результат")
    result = results.get_result(conn, actor, ev["id"])
    if result:
        score = results.score_line(result["our_score"], result["their_score"], ev["opponent"])
        st.markdown(f"**{md(score)}**")
        if result["goals"]:
            # Автор жирным, передающий в скобках (FR-47).
            lines = [
                f"{i}. " + results.goal_line(g["scorer_name"], g["assist_name"], escape=md)
                for i, g in enumerate(result["goals"], start=1)
            ]
            st.markdown("Голы:  \n" + "  \n".join(lines))
        else:
            st.caption("Голы не указаны.")
        if actor.is_organizer and result["warning"]:
            st.warning(result["warning"])
    else:
        st.caption("Результат не внесён.")

    if not actor.is_organizer or ev["status"] == "cancelled":
        return
    if not events.is_started(ev):
        st.caption("Результат можно внести после начала игры.")
        return
    squad = squads.get_squad(conn, actor, ev["id"])
    if squad["state"] != "published":
        st.caption("Результат вносится для игры с опубликованным составом.")
        return
    by_id = {p["id"]: p for p in squad["players"]}
    old_goals = result["goals"] if result else []
    with st.expander("Изменить результат" if result else "Внести результат", expanded=not result):
        rows = st.number_input(
            "Число голов в списке", min_value=0, max_value=results.MAX_SCORE,
            value=len(old_goals), step=1, key=f"res_rows_{ev['id']}",
        )
        with st.form(f"result_form_{ev['id']}"):
            cols = st.columns(2)
            our = cols[0].number_input(
                "Счёт нашей команды", min_value=0, max_value=results.MAX_SCORE, step=1,
                value=result["our_score"] if result else 0, key=f"res_our_{ev['id']}",
            )
            their = cols[1].number_input(
                "Счёт соперника", min_value=0, max_value=results.MAX_SCORE, step=1,
                value=result["their_score"] if result else 0, key=f"res_their_{ev['id']}",
            )
            goals = []
            scorer_ids = list(by_id)
            assist_ids = [None] + scorer_ids
            for i in range(int(rows)):
                old = old_goals[i] if i < len(old_goals) else None
                cols = st.columns(2)
                scorer = cols[0].selectbox(
                    f"Гол {i + 1}: автор", scorer_ids,
                    index=scorer_ids.index(old["scorer_id"])
                    if old and old["scorer_id"] in by_id else None,
                    format_func=lambda pid: player_label(by_id[pid]),
                    placeholder="Выберите автора", key=f"goal_scorer_{ev['id']}_{i}",
                )
                assist = cols[1].selectbox(
                    f"Гол {i + 1}: передача", assist_ids,
                    index=assist_ids.index(old["assist_id"])
                    if old and old["assist_id"] in assist_ids else 0,
                    format_func=lambda pid: "без передачи" if pid is None else player_label(by_id[pid]),
                    key=f"goal_assist_{ev['id']}_{i}",
                )
                goals.append((scorer, assist))
            submitted = st.form_submit_button("Сохранить результат")
    if submitted:
        try:
            warning = results.save_result(conn, actor, ev["id"], int(our), int(their), goals)
        except ServiceError as exc:
            st.error(str(exc))
            return
        if warning:
            flash("warning", warning)
        else:
            flash("success", "Результат сохранён.")
        st.rerun()


def reply_by_inputs(prefix, default_value, default_date):
    """Поля «ответить до»: флажок, дата и время. Возвращает строку или None."""
    use = st.checkbox(
        "Указать срок «ответить до»",
        value=default_value is not None, key=f"{prefix}_use",
    )
    if default_value:
        base = datetime.strptime(default_value, DATETIME_FMT)
        d_value, t_value = base.date(), base.time()
    else:
        d_value, t_value = default_date, time(18, 0)
    reply_date = st.date_input("Ответить до: дата", value=d_value, key=f"{prefix}_date")
    reply_time = st.time_input("Ответить до: время", value=t_value, key=f"{prefix}_time")
    if not use:
        return None
    return datetime.combine(reply_date, reply_time).strftime(DATETIME_FMT)


def gather_time_inputs(prefix, default_value):
    """Поле «время сбора» (FR-41): флажок и время. Возвращает время или None."""
    use = st.checkbox(
        "Указать время сбора", value=default_value is not None, key=f"{prefix}_use",
    )
    value = st.time_input(
        "Время сбора", value=to_time(default_value) if default_value else time(18, 30),
        key=f"{prefix}_time",
    )
    return value if use else None


def show_event_admin(conn, actor, ev):
    with st.expander("Управление событием"):
        if ev["status"] == "planned":
            with st.form(f"edit_event_{ev['id']}"):
                title = st.text_input("Название", value=ev["title"], key=f"e_title_{ev['id']}")
                date_value = st.date_input("Дата", value=to_date(ev["date"]), key=f"e_date_{ev['id']}")
                time_value = st.time_input("Время", value=to_time(ev["time"]), key=f"e_time_{ev['id']}")
                place = st.text_input("Место", value=ev["place"] or "", key=f"e_place_{ev['id']}")
                opponent = None
                if ev["type"] == "game":
                    opponent = st.text_input(
                        "Соперник (необязательно)", value=ev["opponent"] or "",
                        max_chars=events.OPPONENT_MAX_LEN, key=f"e_opponent_{ev['id']}",
                    )
                reply_by = None
                if ev["type"] != "game":
                    reply_by = reply_by_inputs(f"e_reply_{ev['id']}", ev["reply_by"], to_date(ev["date"]))
                gather_time = gather_time_inputs(f"e_gather_{ev['id']}", ev["gather_time"])
                submitted = st.form_submit_button("Сохранить изменения")
            if submitted:
                perform(
                    lambda: events.update_event(
                        conn, actor, ev["id"], title, date_value, time_value, place, reply_by,
                        gather_time=gather_time, opponent=opponent,
                    ),
                    "Изменения сохранены. Отметки явки сохранены.",
                )
        else:
            st.caption("Отменённое событие изменить нельзя.")

        if ev["status"] == "planned" and not events.is_started(ev):
            ok = st.checkbox("Подтверждаю отмену события", key=f"cancel_ok_{ev['id']}")
            if st.button("Отменить событие", disabled=not ok, key=f"cancel_{ev['id']}"):
                perform(lambda: events.cancel_event(conn, actor, ev["id"]), "Событие отменено.")
            st.caption("Отменённое событие остаётся в расписании с пометкой «отменено».")

        ok = st.checkbox("Подтверждаю удаление события", key=f"delete_ok_{ev['id']}")
        if st.button("Удалить событие", disabled=not ok, key=f"delete_{ev['id']}"):
            perform(lambda: events.delete_event(conn, actor, ev["id"]), "Событие удалено.")
        st.caption(
            "Удаление убирает событие, отметки явки, состав и связанные уведомления, "
            "новых уведомлений не создаёт. Игру с результатом удалить нельзя."
        )


# ---------- статистика и уведомления ----------

def show_stats(conn, actor):
    """Статистика игроков за всё время (FR-32, FR-33)."""
    st.header("Статистика")
    rows = results.player_stats(conn, actor)
    if not rows:
        st.info("Статистики пока нет: ни у одной игры не внесён счёт.")
        return
    st.dataframe(
        [
            {"Игрок": player_label(r), "Игры": r["games"], "Голы": r["goals"],
             "Передачи": r["assists"]}
            for r in rows
        ],
        hide_index=True,
    )
    st.caption("«Игры» — игры с внесённым счётом, в составе которых был игрок.")


def show_notifications(conn, actor):
    """Лента уведомлений пользователя (FR-36 – FR-40)."""
    st.header("Уведомления")
    items = notifications.list_notifications(conn, actor)
    if not items:
        st.info("Уведомлений нет.")
        return
    unread = notifications.unread_count(conn, actor)
    st.write(f"Непрочитанных: **{unread}**")
    if st.button("Отметить все прочитанными", disabled=unread == 0):
        perform(lambda: notifications.mark_all_read(conn, actor), "Все уведомления прочитаны.")
    st.caption(f"Показаны последние {notifications.FEED_LIMIT} уведомлений, новые сверху.")
    for n in items:
        when = datetime.strptime(n["created_at"], "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y %H:%M")
        with st.container(border=True):
            cols = st.columns([5, 1])
            if n["is_read"]:
                cols[0].caption(f"{n['kind_label']} · {when} · прочитано")
                cols[0].write(n["text"])
                label = "Открыть"
            else:
                cols[0].markdown(f":blue[**● Новое**] · **{n['kind_label']}** · {when}")
                cols[0].markdown(f"**{n['text']}**")
                label = "Открыть и прочитать"
            if cols[1].button(label, key=f"notif_{n['id']}"):
                try:
                    notifications.mark_read(conn, actor, n["id"])
                except ServiceError as exc:
                    st.error(str(exc))
                else:
                    st.session_state["goto_page"] = PAGE_SCHEDULE
                    st.session_state["goto_event"] = n["event_id"]
                    st.rerun()


# ---------- новое событие ----------

def show_new_event(conn, actor):
    st.header("Новое событие")
    ver = st.session_state.setdefault("new_event_ver", 0)
    today = datetime.now().date()
    # Тип выбирается вне формы, чтобы набор полей сразу менялся под тип события.
    event_type = st.selectbox(
        "Тип", TYPE_OPTIONS, format_func=lambda k: f"{TYPE_ICONS[k]} {events.EVENT_TYPES[k]}",
        key=f"ne_type_{ver}",
    )
    with st.form(f"new_event_form_{ver}"):
        title = st.text_input("Название", key=f"ne_title_{ver}")
        date_value = st.date_input("Дата", value=today, min_value=today, key=f"ne_date_{ver}")
        time_value = st.time_input("Время", value=time(19, 0), key=f"ne_time_{ver}")
        place = st.text_input("Место (необязательно)", key=f"ne_place_{ver}")
        opponent = None
        if event_type == "game":
            opponent = st.text_input(
                "Соперник (необязательно)", max_chars=events.OPPONENT_MAX_LEN,
                key=f"ne_opponent_{ver}",
            )
        reply_by = None
        if event_type != "game":
            reply_by = reply_by_inputs(f"ne_reply_{ver}", None, today)
        gather_time = gather_time_inputs(f"ne_gather_{ver}", None)
        submitted = st.form_submit_button("Создать событие")
    if submitted:
        try:
            new_id = events.create_event(
                conn, actor, event_type, title, date_value, time_value, place, reply_by,
                gather_time=gather_time, opponent=opponent,
            )
        except ServiceError as exc:
            st.error(str(exc))
            return
        st.session_state["new_event_ver"] = ver + 1
        st.session_state["goto_page"] = PAGE_SCHEDULE
        st.session_state["goto_event"] = new_id
        flash("success", "Событие создано.")
        st.rerun()


# ---------- игроки ----------

def show_players(conn, actor):
    st.header("Игроки")
    plist = players.list_players(conn, actor)
    if plist:
        st.dataframe(
            [
                {
                    "Имя": p["name"],
                    "Логин": p["login"] or "",
                    "Номер": "" if p["number"] is None else p["number"],
                    "Позиция": p["position"] or "",
                }
                for p in plist
            ],
            hide_index=True,
        )
    else:
        st.info("Игроков пока нет.")

    st.subheader("Новый игрок")
    ver = st.session_state.setdefault("new_player_ver", 0)
    with st.form(f"new_player_form_{ver}"):
        name = st.text_input("Имя", key=f"np_name_{ver}")
        login_name = st.text_input("Логин", key=f"np_login_{ver}")
        password = st.text_input("Пароль", type="password", key=f"np_password_{ver}")
        number = st.text_input("Игровой номер (необязательно)", key=f"np_number_{ver}")
        position = st.text_input("Позиция (необязательно)", key=f"np_position_{ver}")
        submitted = st.form_submit_button("Создать игрока и аккаунт")
    if submitted:
        try:
            players.create_player_account(
                conn, actor, name, login_name, password, number, position
            )
        except ServiceError as exc:
            st.error(str(exc))
        else:
            st.session_state["new_player_ver"] = ver + 1
            flash("success", "Игрок создан. Передайте ему логин и пароль.")
            st.rerun()

    if not plist:
        return
    st.subheader("Изменить или удалить игрока")
    by_id = {p["id"]: p for p in plist}
    if st.session_state.get("player_select") not in by_id:
        st.session_state["player_select"] = plist[0]["id"]
    pid = st.selectbox(
        "Игрок", list(by_id), key="player_select",
        format_func=lambda i: by_id[i]["name"],
    )
    p = by_id[pid]

    with st.form(f"edit_player_{pid}"):
        new_name = st.text_input("Имя", value=p["name"], key=f"ep_name_{pid}")
        new_number = st.text_input(
            "Игровой номер (необязательно)",
            value="" if p["number"] is None else str(p["number"]), key=f"ep_number_{pid}",
        )
        new_position = st.text_input(
            "Позиция (необязательно)", value=p["position"] or "", key=f"ep_position_{pid}"
        )
        saved = st.form_submit_button("Сохранить данные игрока")
    if saved:
        perform(
            lambda: players.update_player(conn, actor, pid, new_name, new_number, new_position),
            "Данные игрока сохранены.",
        )

    with st.form(f"password_form_{pid}", clear_on_submit=True):
        new_password = st.text_input("Новый пароль игрока", type="password", key=f"pw_{pid}")
        pw_saved = st.form_submit_button("Задать пароль")
    if pw_saved:
        perform(
            lambda: players.set_player_password(conn, actor, pid, new_password),
            "Пароль изменён. Передайте его игроку.",
        )

    ok = st.checkbox("Подтверждаю удаление игрока", key=f"del_player_ok_{pid}")
    if st.button("Удалить игрока", disabled=not ok, key=f"del_player_{pid}"):
        perform(lambda: players.delete_player(conn, actor, pid), "Игрок удалён.")
    st.caption("Игрока, включённого в состав на игру или с голом или передачей, удалить нельзя.")


# ---------- точка входа ----------

def main():
    st.set_page_config(page_title=PAGE_TITLE, page_icon=str(LOGO), layout="wide")
    conn = connect()
    try:
        show_flash()
        actor = st.session_state.get("actor")
        if actor is None:
            show_login(conn)
            return
        try:
            actor = auth.require_actor(conn, actor)
        except ServiceError:
            st.session_state.clear()
            st.warning("Сессия недействительна. Войдите в систему заново.")
            show_login(conn)
            return
        st.session_state["actor"] = actor
        page = show_sidebar(conn, actor)
        if st.session_state.pop("just_logged_in", False):
            # FR-39: число непрочитанных уведомлений сразу после входа.
            unread = notifications.unread_count(conn, actor)
            if unread:
                st.info(f"Непрочитанных уведомлений: {unread}. Откройте раздел «Уведомления».")
            else:
                st.info("Новых уведомлений нет.")
        if page == PAGE_STATS:
            show_stats(conn, actor)
        elif page == PAGE_NOTIFICATIONS:
            show_notifications(conn, actor)
        elif page == PAGE_NEW_EVENT and actor.is_organizer:
            show_new_event(conn, actor)
        elif page == PAGE_PLAYERS and actor.is_organizer:
            show_players(conn, actor)
        else:
            show_schedule(conn, actor)
    finally:
        conn.close()


try:
    main()
except ServiceError as exc:
    st.error(str(exc))
except Exception:  # непредвиденная ошибка: подробности только в журнале
    logger.exception("Непредвиденная ошибка")
    st.error(
        "Не удалось выполнить действие из-за внутренней ошибки. "
        "Обновите страницу и повторите. Если ошибка повторяется, сообщите автору."
    )
