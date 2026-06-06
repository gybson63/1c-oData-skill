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
from typing import Any

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

    default_top: int = Field(default=20, description="Количество записей по умолчанию ($top); подсказка в guard")
    max_top: int = Field(default=50, description="Максимум записей ($top и явный лимит «до N» в запросе пользователя)")
    request_timeout: int = Field(default=60, description="Таймаут HTTP-запросов, сек")
    max_url_length: int = Field(default=2000, description="Максимальная длина URL")
    max_expand_fields: int = Field(default=15, description="Максимальное число полей в $expand")
    max_sample_records: int = Field(default=30, description="Максимальное число записей для AI")
    max_data_length: int = Field(default=8000, description="Максимальная длина данных для AI")
    metadata_cache_seconds: int = Field(default=86400, description="TTL кэша метаданных, сек")
    max_analytics_records: int = Field(default=500, description="Лимит строк на sub-query в analytics")
    max_analytics_joins: int = Field(default=3, description="Максимум join-ов в analytics")
    chart_max_categories: int = Field(default=30, description="Максимум категорий на bar/pie графике")


class AISettings(BaseModel):
    """Настройки AI-провайдера (OpenAI-совместимый API)."""

    api_key: str = Field(default="", description="API-ключ OpenAI")
    base_url: str | None = Field(default=None, description="Кастомный URL API")
    model: str = Field(default="gpt-4o-mini", description="Модель AI")
    rpm: int = Field(default=20, description="Запросов в минуту (rate limit)")
    temperature: float = Field(default=0.1, description="Температура для Шага 1")
    temperature_step2: float = Field(default=0.3, description="Температура для Шага 2")
    timeout_retry_count: int = Field(default=2, description="Повторы при таймауте AI (доп. попытки)")
    timeout_retry_delay: int = Field(default=3, description="Пауза перед повтором при таймауте AI, сек")


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
    proxy_url: str | None = Field(
        default=None,
        description="HTTP/SOCKS5 прокси для api.telegram.org, например http://127.0.0.1:7890",
    )
    use_env_proxy: bool = Field(
        default=True,
        description="Использовать переменные окружения HTTP(S)_PROXY (httpx trust_env)",
    )
    base_url: str | None = Field(
        default=None,
        description="Кастомный Bot API URL (локальный сервер Telegram Bot API)",
    )
    base_file_url: str | None = Field(
        default=None,
        description="Кастомный URL для файлов Telegram Bot API",
    )


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


class ModelPricing(BaseModel):
    """Цены для конкретной модели AI за 1M токенов (USD)."""

    input_per_1m: float = Field(description="Цена входных токенов за 1M")
    output_per_1m: float = Field(description="Цена выходных токенов за 1M")


class PricingSettings(BaseModel):
    """Стоимость AI-запросов за 1M токенов (USD).

    Поддерживает per-model ценообразование через ``per_model``.
    Если модель не найдена в ``per_model``, используются дефолтные цены.
    """

    input_per_1m: float = Field(default=0.15, description="Цена входных токенов за 1M (default)")
    output_per_1m: float = Field(default=0.60, description="Цена выходных токенов за 1M (default)")
    per_model: dict[str, ModelPricing] = Field(
        default_factory=dict,
        description="Цены по моделям: {model_name: {input_per_1m, output_per_1m}}",
    )

    def get_prices(self, model: str) -> tuple[float, float]:
        """Вернуть (input_per_1m, output_per_1m) для модели или дефолт."""
        if model in self.per_model:
            mp = self.per_model[model]
            return mp.input_per_1m, mp.output_per_1m
        return self.input_per_1m, self.output_per_1m


class EmailSettings(BaseModel):
    """Настройки email-транспорта (IMAP/SMTP)."""

    enabled: bool = Field(default=False, description="Включить email-транспорт")
    imap_host: str = Field(default="", description="IMAP-сервер")
    imap_port: int = Field(default=993, description="IMAP-порт")
    imap_user: str = Field(default="", description="IMAP-логин")
    imap_password: str = Field(default="", description="IMAP-пароль")
    imap_folder: str = Field(default="INBOX", description="Папка для чтения")
    imap_use_ssl: bool = Field(default=True, description="IMAP через SSL")
    smtp_host: str = Field(default="", description="SMTP-сервер")
    smtp_port: int = Field(default=587, description="SMTP-порт")
    smtp_user: str = Field(default="", description="SMTP-логин")
    smtp_password: str = Field(default="", description="SMTP-пароль")
    smtp_use_ssl: bool = Field(default=False, description="SMTP через SSL (порт 465)")
    smtp_use_tls: bool = Field(default=True, description="SMTP STARTTLS (порт 587)")
    from_address: str = Field(default="", description="Адрес отправителя (From)")
    from_name: str = Field(default="1С OData Bot", description="Имя отправителя")
    message_id_domain: str = Field(default="odata-bot.local", description="Домен для Message-ID")
    poll_interval: int = Field(default=30, description="Интервал опроса IMAP, сек")
    allowed_senders: list[str] = Field(
        default_factory=list,
        description="Разрешённые отправители (пусто = все)",
    )
    context_max_chars: int = Field(default=12000, description="Макс. символов контекста цепочки")
    context_message_max_chars: int = Field(default=3000, description="Макс. символов одного письма")
    context_keep_recent: int = Field(default=3, description="Последние N писем — полностью")
    context_keep_first: bool = Field(default=True, description="Первое письмо — всегда полностью")
    context_middle_summary_chars: int = Field(default=300, description="Сжатие средних писем до N символов")
    inline_max_chars: int = Field(
        default=8000,
        description="Макс. символов ответа в теле письма; при превышении — вложение",
    )
    inline_preview_chars: int = Field(default=500, description="Символов превью в теле при отправке вложения")
    attachment_filename: str = Field(
        default="",
        description="Имя файла вложения (пусто = автогенерация из темы)",
    )
    attachment_format: str = Field(default="html", description="Формат вложения: html")
    max_fetch_records: int = Field(
        default=500,
        description="Макс. записей OData при автозагрузке всех страниц для email",
    )


class HistorySettings(BaseModel):
    """Настройки управления историей диалогов."""

    max_turns: int = Field(default=10, description="Максимальное число пар в истории (для AI-контекста)")
    max_messages: int = Field(default=100, description="Абсолютный максимум сообщений на чат (safety net)")
    trim_to: int = Field(default=60, description="Число сообщений при обрезке (при достижении max_messages)")
    persist_dir: str | None = Field(
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
    email: EmailSettings = Field(default_factory=EmailSettings)
    ai_pricing: PricingSettings = Field(default_factory=PricingSettings)

    # Общие настройки
    cache_dir: str = Field(default=".cache", description="Директория для кэша")
    log_level: str = Field(default="INFO", description="Уровень логирования")
    log_file: str | None = Field(default=None, description="Путь к файлу лога")
    history_max_turns: int = Field(
        default=10, description="Максимальное число пар в истории (legacy, используйте history.max_turns)"
    )

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

_settings: AppSettings | None = None


def get_settings() -> AppSettings:
    """Получить текущую конфигурацию.

    Raises:
        RuntimeError: если load_settings() ещё не вызывался.
    """
    if _settings is None:
        raise RuntimeError("Settings not loaded. Call load_settings() first.")
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
        timeout_retry_count=p.get("ai_timeout_retry_count", 2),
        timeout_retry_delay=p.get("ai_timeout_retry_delay", 3),
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
        proxy_url=tg_raw.get("proxy_url"),
        use_env_proxy=tg_raw.get("use_env_proxy", True),
        base_url=tg_raw.get("base_url"),
        base_file_url=tg_raw.get("base_file_url"),
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
        max_analytics_records=odata_raw.get("max_analytics_records", 500),
        max_analytics_joins=odata_raw.get("max_analytics_joins", 3),
        chart_max_categories=odata_raw.get("chart_max_categories", 30),
    )

    # --- Formatter ---
    fmt_raw = p.get("formatter", {})
    formatter = FormatterSettings(
        enabled=fmt_raw.get("enabled", True),
        formatter_model=fmt_raw.get("formatter_model", "gpt-4o-mini"),
        temperature=fmt_raw.get("temperature", 0.2),
    )

    # --- Email ---
    email_raw = p.get("email", {})
    email = EmailSettings(
        enabled=email_raw.get("enabled", False),
        imap_host=email_raw.get("imap_host", ""),
        imap_port=email_raw.get("imap_port", 993),
        imap_user=email_raw.get("imap_user", ""),
        imap_password=email_raw.get("imap_password", ""),
        imap_folder=email_raw.get("imap_folder", "INBOX"),
        imap_use_ssl=email_raw.get("imap_use_ssl", True),
        smtp_host=email_raw.get("smtp_host", ""),
        smtp_port=email_raw.get("smtp_port", 587),
        smtp_user=email_raw.get("smtp_user", ""),
        smtp_password=email_raw.get("smtp_password", ""),
        smtp_use_ssl=email_raw.get("smtp_use_ssl", False),
        smtp_use_tls=email_raw.get("smtp_use_tls", True),
        from_address=email_raw.get("from_address", ""),
        from_name=email_raw.get("from_name", "1С OData Bot"),
        message_id_domain=email_raw.get("message_id_domain", "odata-bot.local"),
        poll_interval=email_raw.get("poll_interval", 30),
        allowed_senders=email_raw.get("allowed_senders", []),
        context_max_chars=email_raw.get("context_max_chars", 12000),
        context_message_max_chars=email_raw.get("context_message_max_chars", 3000),
        context_keep_recent=email_raw.get("context_keep_recent", 3),
        context_keep_first=email_raw.get("context_keep_first", True),
        context_middle_summary_chars=email_raw.get("context_middle_summary_chars", 300),
        inline_max_chars=email_raw.get("inline_max_chars", 8000),
        inline_preview_chars=email_raw.get("inline_preview_chars", 500),
        attachment_filename=email_raw.get("attachment_filename", ""),
        attachment_format=email_raw.get("attachment_format", "html"),
        max_fetch_records=email_raw.get("max_fetch_records", 500),
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

    # --- AI Pricing ---
    pricing_raw = p.get("ai_pricing", {})
    per_model_raw = pricing_raw.get("per_model", {})
    per_model = {
        name: ModelPricing(
            input_per_1m=mp.get("input_per_1m", 0.15),
            output_per_1m=mp.get("output_per_1m", 0.60),
        )
        for name, mp in per_model_raw.items()
    }
    ai_pricing = PricingSettings(
        input_per_1m=pricing_raw.get("input_per_1m", 0.15),
        output_per_1m=pricing_raw.get("output_per_1m", 0.60),
        per_model=per_model,
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
        email=email,
        ai_pricing=ai_pricing,
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
