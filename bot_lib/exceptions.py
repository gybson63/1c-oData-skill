#!/usr/bin/env python3
"""Иерархия исключений проекта 1c-oData-skill.

Базовый уровень: :class:`ODataSkillError` — от него наследуются
три ветки: OData, AI и конфигурация.

.. code-block:: text

    ODataSkillError
    ├── ODataError
    │   ├── ODataConnectionError
    │   ├── ODataHTTPError
    │   └── ODataParseError
    ├── AIError
    │   ├── AIRateLimitError
    │   └── AIResponseError
    └── ConfigError
"""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════════════════════
# Корень иерархии
# ═══════════════════════════════════════════════════════════════════════════


class ODataSkillError(Exception):
    """Базовое исключение всего проекта.

    Перехват ``ODataSkillError`` гарантирует обработку любой
    типизированной ошибки, но не «голых» ``Exception`` / ``ValueError``.
    """


# ═══════════════════════════════════════════════════════════════════════════
# OData-ошибки
# ═══════════════════════════════════════════════════════════════════════════


class ODataError(ODataSkillError):
    """Ошибка при работе с OData API 1С.

    Обратно совместимо с прежним классом из
    ``bot.agents.odata.odata_http`` — принимает ``message``
    и опциональный ``status_code``.
    """

    def __init__(self, message: str = "", status_code: int = 0) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class ODataConnectionError(ODataError):
    """Не удалось подключиться к 1С OData (timeout, DNS, connection refused)."""

    def __init__(self, message: str = "", status_code: int = 0) -> None:
        super().__init__(message, status_code)


class ODataHTTPError(ODataError):
    """HTTP-ошибка от 1С OData (4xx, 5xx)."""

    def __init__(
        self,
        message: str = "",
        status_code: int = 0,
        url: str = "",
    ) -> None:
        self.url = url
        super().__init__(message, status_code)


class ODataParseError(ODataError):
    """Ошибка парсинга ответа OData (JSON/XML)."""


# ═══════════════════════════════════════════════════════════════════════════
# AI-ошибки
# ═══════════════════════════════════════════════════════════════════════════


class AIError(ODataSkillError):
    """Ошибка при работе с AI-провайдером (OpenAI-совместимый API).

    Оборачивает ошибки сети, авторизации и неожиданных ответов AI.
    """


class AIRateLimitError(AIError):
    """Превышен лимит запросов к AI API (429 Too Many Requests)."""


class AIResponseError(AIError):
    """Неожиданный или невалидный ответ от AI (пустой ответ, невалидный JSON)."""


# ═══════════════════════════════════════════════════════════════════════════
# Конфигурация
# ═══════════════════════════════════════════════════════════════════════════


class ConfigError(ODataSkillError):
    """Ошибка конфигурации (отсутствует env.json, неверный формат, отсутствие профиля)."""
