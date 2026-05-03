#!/usr/bin/env python3
"""Централизованная конфигурация проекта через Pydantic Settings.

Заменяет ручное чтение env.json и разбросанные dict-доступы
типизированными моделями с валидацией.

Использование::

    from bot.config import load_settings, get_settings

    # При старте приложения:
    settings = load_settings("env.json", "default")

    # В любом модуле:
    settings = get_settings()
    print(settings.ai.model)
    print(settings.bot.token)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from bot_lib.exceptions import ConfigError

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Settings models
# ---------------------------------------------------------------------------


class ODataConnectionSettings(BaseModel):
    """Настройки подключения к 1С OData (per-agent)."""

    url: str = Field(default="", description="Base URL OData")
    user: str = Field(default="", description="Имя пользователя 1С")
    password: str = Field(default="", description="Пароль пользователя 1С")


class ODataQuerySettings(BaseModel):
    """Настройки ограничений OData-запросов."""

    default_top: int = Field(default=20, description="Количество записей по умолчанию ($top)")
    max_top: int = Field(default=50, description="Максимальное количество записей")
    request_timeout: int = Field(default=60, description="Таймаут HTTP-запросов, сек")
    max_url_length: int = Field(default=2000, description="Максимальная длина URL")
    max_expand_fields: int = Field(default=15, description="Максимальное число полей в $expand")
    max_sample_records: int = Field(default=30, description="Максимальное число записей для AI")
    max_data_length: int = Field(default=8000, description="Максимальная длина данных для AI")
    metadata_cache_seconds: int = Field(default=86400, description="TTL кэша метаданных, сек")


class AISettings(BaseModel):
    """Настройки AI-провайдера (OpenAI-совместимый API)."""

    api_key: str = Field(default="", description="API-ключ OpenAI")
    base_url: Optional[str] = Field(default=None, description="Кастомный URL API")
    model: str = Field(default="gpt-4o-mini", description="Модель AI")
    rpm: int = Field(default=20, description="Запросов в минуту (rate limit)")
    temperature: float = Field(default=0.1, description="Температура для Шага 1")
    temperature_step2: float = Field(default=0.3, description="Температура для Шага 2")


class BotSettings(BaseModel):
    """Настройки Telegram-бота."""

    token: str = Field(default="", description="Токен Telegram-бота")


class TelegramTransportSettings(BaseModel):
    """Настройки транспорта Telegram API."""

    message_max_length: int = Field(default=4000, description="Максимальная длина сообщения")
    connect_timeout: int = Field(default=30, description="Таймаут подключения, сек")
    read_timeout: int = Field(default=120, description="Таймаут чтения, сек")
    write_timeout: int = Field(default=60, description="Таймаут записи, сек")
    retry_count: int = Field(default=2, description="Количество ретраев при отправке")
    retry_delay: int = Field(default=2, description="Задержка между ретраями, сек")
    polling_restart_delay: int = Field(default=5, description="Задержка рестарта polling, сек")


class FormatterSettings(BaseModel):
    """Настройки агента-форматтера."""

    enabled: bool = Field(default=True, description="Включить форматирование")
    formatter_model: str = Field(default="gpt-4o-mini", description="Модель AI для форматирования")
    temperature: float = Field(default=0.2, description="Температура для форматирования")


class MCPConfig(BaseModel):
    """Настройки одного MCP-сервера."""

    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class HistorySettings(BaseModel):
    """Настройки управления историей диалогов."""

    max_turns: int = Field(default=10, description="Максимальное число пар в истории (для AI-контекста)")
    max_messages: int = Field(default=100, description="Абсолютный максимум сообщений на чат (safety net)")
    trim_to: int = Field(default=60, description="Число сообщений при обрезке (при достижении max_messages)")
    persist_dir: Optional[str] = Field(
        default=None,
        description="Директория для сохранения историй на диск (None = только в памяти)",
    )


class AppSettings(BaseModel):
    """Главный конфиг приложения — все настройки в одном месте."""

    # Основные секции
    ai: AISettings = Field(default_factory=AISettings)
    bot: BotSettings = Field(default_factory=BotSettings)
    telegram: TelegramTransportSettings = Field(default_factory=TelegramTransportSettings)
    odata_query: ODataQuerySettings = Field(default_factory=ODataQuerySettings)
    formatter: FormatterSettings = Field(default_factory=FormatterSettings)
    history: HistorySettings = Field(default_factory=HistorySettings)

    # Общие настройки
    cache_dir: str = Field(default=".cache", description="Директория для кэша")
    log_level: str = Field(default="INFO", description="Уровень логирования")
    log_file: Optional[str] = Field(default=None, description="Путь к файлу лога")
    history_max_turns: int = Field(default=10, description="Максимальное число пар в истории (legacy, используйте history.max_turns)")

    # Agents config (сырой dict — для передачи в BaseAgent.initialize)
    agents_config: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Конфигурация агентов (секция 'agents' из env.json)",
    )

    # Profile-level data needed for agent init backward compat
    _profile_raw: dict[str, Any] = {}

    class Config:
        arbitrary_types_allowed = True


# ---------------------------------------------------------------------------
# Module-level state (singleton)
# ---------------------------------------------------------------------------

_settings: Optional[AppSettings] = None


def get_settings() -> AppSettings:
    """Получить текущую конфигурацию.

    Raises:
        RuntimeError: если load_settings() ещё не вызывался.
    """
    if _settings is None:
        raise RuntimeError(
            "Settings not loaded. Call load_settings() first."
        )
    return _settings


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_settings(
    env_file: str = "env.json",
    profile: str = "default",
) -> AppSettings:
    """Загрузить конфигурацию из env.json и вернуть типизированный объект.

    Формат env.json::

        {
          "profiles": {
            "default": {
              "telegram_token": "...",
              "ai_api_key": "...",
              "odata": { ... },
              "telegram": { ... },
              "agents": { ... }
            }
          }
        }

    Args:
        env_file: путь к файлу конфигурации.
        profile: имя профиля (ключ в секции ``profiles``).

    Returns:
        Типизированный объект :class:`AppSettings`.

    Raises:
        FileNotFoundError: если файл не найден.
        ValueError: если профиль не существует.
    """
    global _settings

    path = Path(env_file)
    if not path.exists():
        raise ConfigError(f"Конфигурация не найдена: {env_file}")

    try:
        data = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ConfigError(f"Ошибка чтения конфигурации {env_file}: {exc}") from exc

    # Поддержка двух форматов:
    # 1. {"profiles": {"default": {...}}} — текущий формат
    # 2. {"default": {...}} — старый формат (из IMPROVEMENT_PLAN.md)
    if "profiles" in data:
        profiles = data["profiles"]
    else:
        profiles = data

    if profile not in profiles:
        available = ", ".join(profiles.keys()) or "(нет)"
        raise ValueError(f"Профиль '{profile}' не найден. Доступные: {available}")

    p = profiles[profile]

    settings = _build_settings(p)
    _settings = settings

    log.info(
        "Конфигурация загружена: profile=%s, ai_model=%s, bot_token=%s...%s",
        profile,
        settings.ai.model,
        settings.bot.token[:4] if settings.bot.token else "(empty)",
        settings.bot.token[-4:] if len(settings.bot.token) > 4 else "",
    )
    return settings


def _build_settings(p: dict[str, Any]) -> AppSettings:
    """Собрать AppSettings из сырого dict профиля."""

    # --- AI ---
    ai = AISettings(
        api_key=p.get("ai_api_key", ""),
        base_url=p.get("ai_base_url"),
        model=p.get("ai_model", "gpt-4o-mini"),
        rpm=p.get("ai_rpm", 20),
        temperature=p.get("ai_temperature", 0.1),
        temperature_step2=p.get("ai_temperature_step2", 0.3),
    )

    # --- Bot ---
    bot = BotSettings(
        token=p.get("telegram_token", ""),
    )

    # --- Telegram transport ---
    tg_raw = p.get("telegram", {})
    telegram = TelegramTransportSettings(
        message_max_length=tg_raw.get("message_max_length", 4000),
        connect_timeout=tg_raw.get("connect_timeout", 30),
        read_timeout=tg_raw.get("read_timeout", 120),
        write_timeout=tg_raw.get("write_timeout", 60),
        retry_count=tg_raw.get("retry_count", 2),
        retry_delay=tg_raw.get("retry_delay", 2),
        polling_restart_delay=tg_raw.get("polling_restart_delay", 5),
    )

    # --- OData query limits ---
    odata_raw = p.get("odata", {})
    odata_query = ODataQuerySettings(
        default_top=odata_raw.get("default_top", 20),
        max_top=odata_raw.get("max_top", 50),
        request_timeout=odata_raw.get("request_timeout", 60),
        max_url_length=odata_raw.get("max_url_length", 2000),
        max_expand_fields=odata_raw.get("max_expand_fields", 15),
        max_sample_records=odata_raw.get("max_sample_records", 30),
        max_data_length=odata_raw.get("max_data_length", 8000),
        metadata_cache_seconds=odata_raw.get("metadata_cache_seconds", 86400),
    )

    # --- Formatter ---
    fmt_raw = p.get("formatter", {})
    formatter = FormatterSettings(
        enabled=fmt_raw.get("enabled", True),
        formatter_model=fmt_raw.get("formatter_model", "gpt-4o-mini"),
        temperature=fmt_raw.get("temperature", 0.2),
    )

    # --- History ---
    hist_raw = p.get("history", {})
    # Поддержка legacy-ключа history_max_turns на верхнем уровне
    legacy_max_turns = p.get("history_max_turns", 10)
    history = HistorySettings(
        max_turns=hist_raw.get("max_turns", legacy_max_turns),
        max_messages=hist_raw.get("max_messages", 100),
        trim_to=hist_raw.get("trim_to", 60),
        persist_dir=hist_raw.get("persist_dir"),
    )

    # --- Agents config (raw, for backward compatibility with BaseAgent.initialize) ---
    agents_config = p.get("agents", {})

    return AppSettings(
        ai=ai,
        bot=bot,
        telegram=telegram,
        odata_query=odata_query,
        formatter=formatter,
        history=history,
        cache_dir=p.get("cache_dir", ".cache"),
        log_level=p.get("log_level", "INFO"),
        log_file=p.get("log_file"),
        history_max_turns=history.max_turns,
        agents_config=agents_config,
    )


# ---------------------------------------------------------------------------
# Helpers for backward compatibility with BaseAgent.initialize(dict, dict)
# ---------------------------------------------------------------------------


def build_global_config(settings: AppSettings) -> dict[str, Any]:
    """Собрать ``global_config`` dict для передачи в :meth:`BaseAgent.initialize`.

    Агенты, которые ещё не мигрировали на ``get_settings()``, могут
    использовать этот dict для получения базовых настроек AI.
    """
    return {
        "ai_api_key": settings.ai.api_key,
        "ai_base_url": settings.ai.base_url,
        "ai_model": settings.ai.model,
        "ai_rpm": settings.ai.rpm,
        "ai_temperature": settings.ai.temperature,
        "ai_temperature_step2": settings.ai.temperature_step2,
        "history_max_turns": settings.history_max_turns,
    }


def get_agent_setting(
    agent_config: dict[str, Any],
    key: str,
    settings_attr: Any = None,
    default: Any = None,
) -> Any:
    """Получить настройку из agent_config, fallback к settings, fallback к default.

    Используется для постепенной миграции: если агент получил typed settings —
    берём из него, иначе — из legacy dict.
    """
    if key in agent_config:
        return agent_config[key]
    if settings_attr is not None:
        return settings_attr
    return default