"""Соперник у игры (FR-9) и вид результата в карточке игры (FR-47)."""
import pytest

from teamplan import events, results
from teamplan.errors import ServiceError
from teamplan.settings import TEAM_NAME


def test_game_with_opponent(conn, org, mk_event):
    game = mk_event("game", opponent="  Байкальские соколы  ")
    assert events.get_event(conn, org, game)["opponent"] == "Байкальские соколы"


@pytest.mark.parametrize("value", [None, "", "   ", "​"])
def test_opponent_is_optional(conn, org, mk_event, value):
    game = mk_event("game", opponent=value)
    assert events.get_event(conn, org, game)["opponent"] is None


def test_opponent_length(conn, org, mk_event):
    game = mk_event("game", opponent="С" * 100)
    assert events.get_event(conn, org, game)["opponent"] == "С" * 100
    with pytest.raises(ServiceError, match="Соперник: не более 100 символов"):
        mk_event("game", opponent="С" * 101)


@pytest.mark.parametrize("event_type", ["training", "activity"])
def test_opponent_only_for_game(conn, org, mk_event, event_type):
    with pytest.raises(ServiceError, match="только для игры"):
        mk_event(event_type, opponent="Байкальские соколы")
    # Без соперника тренировка и мероприятие создаются как раньше.
    ev = mk_event(event_type)
    assert events.get_event(conn, org, ev)["opponent"] is None


def test_opponent_is_changed_and_cleared(conn, org, mk_event, now):
    game = mk_event("game", opponent="Старый соперник")
    ev = events.get_event(conn, org, game)

    def update(opponent):
        events.update_event(conn, org, game, ev["title"], ev["date"], ev["time"],
                            ev["place"], now=now, opponent=opponent)
        return events.get_event(conn, org, game)["opponent"]

    assert update("Новый соперник") == "Новый соперник"
    assert update("") is None
    with pytest.raises(ServiceError, match="не более 100 символов"):
        update("С" * 101)


def test_opponent_cannot_be_set_on_training_update(conn, org, mk_event, now):
    training = mk_event("training")
    ev = events.get_event(conn, org, training)
    with pytest.raises(ServiceError, match="только для игры"):
        events.update_event(conn, org, training, ev["title"], ev["date"], ev["time"],
                            now=now, opponent="Байкальские соколы")


def test_score_line_with_opponent():
    assert results.score_line(3, 1, "Байкальские соколы") == (
        f"Счёт: 3 : 1 ({TEAM_NAME} : Байкальские соколы)"
    )
    assert TEAM_NAME == "ХК «Иркутский Политех»"


@pytest.mark.parametrize("opponent", [None, ""])
def test_score_line_without_opponent(opponent):
    assert results.score_line(0, 2, opponent) == f"Счёт: 0 : 2 ({TEAM_NAME} : Соперник)"


def test_goal_line_with_assist():
    assert results.goal_line("Игрок Первый", "Игрок Второй") == "**Игрок Первый** (Игрок Второй)"


def test_goal_line_without_assist():
    assert results.goal_line("Игрок Первый", None) == "**Игрок Первый**"


def test_goal_line_escapes_names():
    def escape(text):
        return text.replace("_", r"\_")

    assert results.goal_line("Игрок_1", "Игрок_2", escape=escape) == r"**Игрок\_1** (Игрок\_2)"
