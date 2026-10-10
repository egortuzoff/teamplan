"""Общие функции: даты, время, проверка текстовых полей."""
import unicodedata
from datetime import date, datetime, time

from .errors import ServiceError

DATE_FMT = "%Y-%m-%d"
TIME_FMT = "%H:%M"
DATETIME_FMT = "%Y-%m-%d %H:%M"


def current_time(now=None):
    """Текущее время; в тестах подставляется фиксированное значение."""
    return now or datetime.now()


def stamp(now=None):
    """Метка времени для служебных полей (создано, изменено)."""
    return current_time(now).strftime("%Y-%m-%d %H:%M:%S")


def norm_date(value, label="Дата"):
    """Приводит дату к строке ГГГГ-ММ-ДД или сообщает об ошибке."""
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime(DATE_FMT)
    if isinstance(value, str):
        try:
            return datetime.strptime(value.strip(), DATE_FMT).strftime(DATE_FMT)
        except ValueError:
            pass
    raise ServiceError(f"{label}: укажите дату в формате ГГГГ-ММ-ДД.")


def norm_time(value, label="Время"):
    """Приводит время к строке ЧЧ:ММ или сообщает об ошибке."""
    if isinstance(value, datetime):
        value = value.time()
    if isinstance(value, time):
        return value.strftime(TIME_FMT)
    if isinstance(value, str):
        try:
            return datetime.strptime(value.strip(), TIME_FMT).strftime(TIME_FMT)
        except ValueError:
            pass
    raise ServiceError(f"{label}: укажите время в формате ЧЧ:ММ.")


def norm_optional_datetime(value, label):
    """Необязательные дата и время: пусто -> None, иначе строка ГГГГ-ММ-ДД ЧЧ:ММ."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.strftime(DATETIME_FMT)
    if isinstance(value, str):
        try:
            return datetime.strptime(value.strip(), DATETIME_FMT).strftime(DATETIME_FMT)
        except ValueError:
            pass
    raise ServiceError(f"{label}: укажите дату и время в формате ГГГГ-ММ-ДД ЧЧ:ММ.")


# Символы, которые не видны на экране, но не считаются пробелами в str.strip():
# заполнители хангыль и пустой символ Брайля.
INVISIBLE_CHARS = {"\u115f", "\u1160", "\u3164", "\uffa0", "\u2800"}


def is_blank(text):
    """True, если в тексте нет ни одного видимого символа.

    Пробелы, переводы строк, нулевой ширины пробелы, метки направления текста
    и подобные служебные символы видимым текстом не считаются (FR-10).
    """
    return all(
        ch.isspace()
        or ch in INVISIBLE_CHARS
        or unicodedata.category(ch) in ("Cc", "Cf", "Zs", "Zl", "Zp")
        for ch in text
    )


def clean_text(value, label, max_len, required=True):
    """Обрезает пробелы и проверяет обязательность и длину текста."""
    text = (value or "").strip()
    if is_blank(text):
        if required:
            raise ServiceError(f"{label}: поле обязательно для заполнения.")
        return None
    if len(text) > max_len:
        raise ServiceError(f"{label}: не более {max_len} символов.")
    return text


def parse_number(value):
    """Игровой номер: пусто -> None, иначе целое число от 0 до 999."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None
        if not value.isdigit():
            raise ServiceError("Игровой номер: целое число от 0 до 999.")
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 999:
        raise ServiceError("Игровой номер: целое число от 0 до 999.")
    return value


def event_start(date_str, time_str):
    """Момент начала события."""
    return datetime.strptime(f"{date_str} {time_str}", DATETIME_FMT)


def format_date(date_str):
    """ГГГГ-ММ-ДД -> ДД.ММ.ГГГГ для показа пользователю."""
    return datetime.strptime(date_str, DATE_FMT).strftime("%d.%m.%Y")


def format_datetime(value):
    """ГГГГ-ММ-ДД ЧЧ:ММ -> ДД.ММ.ГГГГ ЧЧ:ММ для показа пользователю."""
    return datetime.strptime(value, DATETIME_FMT).strftime("%d.%m.%Y %H:%M")
