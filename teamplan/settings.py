"""Настройки приложения, общие для экранов и логики."""
import os

# Название нашей команды (FR-47): шапка, страница входа, счёт в карточке игры и на табло.
TEAM_NAME = "ХК «Иркутский Политех»"

# Режим работы (ТР-6): код одинаков, режим задаётся переменной окружения TEAMPLAN_MODE.
# demo — демонстрационный (по умолчанию): при запуске создаются тестовые данные.
# production — рабочий на сервере: тестовые данные не создаются (РЗ-7).
MODE_DEMO = "demo"
MODE_PRODUCTION = "production"
MODES = (MODE_DEMO, MODE_PRODUCTION)


def get_mode():
    """Режим из TEAMPLAN_MODE; неизвестное значение — ошибка, а не тихий демо-режим."""
    mode = os.environ.get("TEAMPLAN_MODE", "").strip().lower() or MODE_DEMO
    if mode not in MODES:
        raise RuntimeError(
            f"Неизвестный режим TEAMPLAN_MODE={mode!r}. Допустимо: demo или production."
        )
    return mode
