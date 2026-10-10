"""TeamPlan: учёт расписания, явки и составов команды (этап 1).

Запуск: streamlit run app.py
Вся логика и проверка прав находятся в пакете teamplan; здесь только экраны.
"""
import logging
from datetime import datetime, time

import streamlit as st

from teamplan import attendance, auth, events, players, squads
from teamplan.common import DATE_FMT, DATETIME_FMT, TIME_FMT, format_date, format_datetime
from teamplan.db import connect
from teamplan.errors import ServiceError

logger = logging.getLogger("teamplan")

PAGE_SCHEDULE = "Расписание"
PAGE_NEW_EVENT = "Новое событие"
PAGE_PLAYERS = "Игроки"

TYPE_OPTIONS = list(events.EVENT_TYPES)


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


def event_label(ev):
    suffix = " (отменено)" if ev["status"] == "cancelled" else ""
    return f"{format_date(ev['date'])} {ev['time']} · {ev['type_label']} · {ev['title']}{suffix}"


def player_label(p):
    return p["name"] + (f" (№{p['number']})" if p["number"] is not None else "")


# ---------- вход ----------

def show_login(conn):
    st.title("TeamPlan")
    st.caption("Расписание, явка и составы команды")
    with st.form("login_form"):
        login_name = st.text_input("Логин")
        password = st.text_input("Пароль", type="password")
        submitted = st.form_submit_button("Войти")
    if submitted:
        try:
            actor = auth.login(conn, login_name, password)
        except ServiceError as exc:
            st.error(str(exc))
            return
        st.session_state["actor"] = actor
        st.rerun()


def show_sidebar(actor):
    st.sidebar.markdown(f"**{actor.name}**")
    st.sidebar.caption(auth.ROLE_LABELS[actor.role])
    pages = [PAGE_SCHEDULE]
    if actor.is_organizer:
        pages += [PAGE_NEW_EVENT, PAGE_PLAYERS]
    # Переход на другую страницу из кода: значение задаём до создания переключателя.
    if "goto_page" in st.session_state:
        st.session_state["page"] = st.session_state.pop("goto_page")
    if st.session_state.get("page") not in pages:
        st.session_state["page"] = pages[0]
    page = st.sidebar.radio("Раздел", pages, key="page")
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
    st.dataframe(
        [
            {
                "Дата": format_date(e["date"]),
                "Время": e["time"],
                "Тип": e["type_label"],
                "Название": e["title"],
                "Место": e["place"] or "",
                "Статус": e["status_label"],
            }
            for e in evs
        ],
        hide_index=True,
    )
    ids = [e["id"] for e in evs]
    by_id = {e["id"]: e for e in evs}
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


def show_event_card(conn, actor, ev):
    st.divider()
    st.subheader(ev["title"])
    line = f"{ev['type_label']} · {format_date(ev['date'])} в {ev['time']}"
    if ev["place"]:
        line += f" · {ev['place']}"
    st.write(line)
    if ev["status"] == "cancelled":
        st.warning("Событие отменено.")
    if ev["type"] == "game":
        show_squad(conn, actor, ev)
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
    cols[0].metric("Буду", counts["yes"])
    cols[1].metric("Под вопросом", counts["maybe"])
    cols[2].metric("Не буду", counts["no"])
    cols[3].metric("Не ответили", counts["none"])

    missing = ", ".join(p["name"] for p in summary["by_status"]["none"])
    if summary["highlight_missing"]:
        st.error(f"Срок ответа прошёл, не ответили: {missing}")
    elif missing:
        st.info(f"Ещё не ответили: {missing}")
    for key in ("yes", "maybe", "no"):
        names = ", ".join(p["name"] for p in summary["by_status"][key])
        if names:
            st.write(f"**{attendance.ATTENDANCE_LABELS[key]}:** {names}")

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
            format_func=lambda k: attendance.ATTENDANCE_LABELS[k],
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


def reply_by_inputs(prefix, default_value, default_date):
    """Поля «ответить до»: флажок, дата и время. Возвращает строку или None."""
    use = st.checkbox(
        "Указать срок «ответить до» (для тренировки и сбора)",
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


def show_event_admin(conn, actor, ev):
    with st.expander("Управление событием"):
        if ev["status"] == "planned":
            with st.form(f"edit_event_{ev['id']}"):
                title = st.text_input("Название", value=ev["title"], key=f"e_title_{ev['id']}")
                date_value = st.date_input("Дата", value=to_date(ev["date"]), key=f"e_date_{ev['id']}")
                time_value = st.time_input("Время", value=to_time(ev["time"]), key=f"e_time_{ev['id']}")
                place = st.text_input("Место", value=ev["place"] or "", key=f"e_place_{ev['id']}")
                reply_by = None
                if ev["type"] != "game":
                    reply_by = reply_by_inputs(f"e_reply_{ev['id']}", ev["reply_by"], to_date(ev["date"]))
                submitted = st.form_submit_button("Сохранить изменения")
            if submitted:
                perform(
                    lambda: events.update_event(
                        conn, actor, ev["id"], title, date_value, time_value, place, reply_by
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
        st.caption("Удаление убирает событие, отметки явки и состав без уведомлений.")


# ---------- новое событие ----------

def show_new_event(conn, actor):
    st.header("Новое событие")
    ver = st.session_state.setdefault("new_event_ver", 0)
    today = datetime.now().date()
    with st.form(f"new_event_form_{ver}"):
        event_type = st.selectbox(
            "Тип", TYPE_OPTIONS, format_func=lambda k: events.EVENT_TYPES[k],
            key=f"ne_type_{ver}",
        )
        title = st.text_input("Название", key=f"ne_title_{ver}")
        date_value = st.date_input("Дата", value=today, min_value=today, key=f"ne_date_{ver}")
        time_value = st.time_input("Время", value=time(19, 0), key=f"ne_time_{ver}")
        place = st.text_input("Место (необязательно)", key=f"ne_place_{ver}")
        reply_by = reply_by_inputs(f"ne_reply_{ver}", None, today)
        submitted = st.form_submit_button("Создать событие")
    if submitted:
        try:
            new_id = events.create_event(
                conn, actor, event_type, title, date_value, time_value, place, reply_by
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
    st.caption("Игрока, включённого в состав на игру, удалить нельзя.")


# ---------- точка входа ----------

def main():
    st.set_page_config(page_title="TeamPlan", page_icon="📅", layout="wide")
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
        page = show_sidebar(actor)
        if page == PAGE_NEW_EVENT and actor.is_organizer:
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
