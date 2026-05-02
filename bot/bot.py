#!/usr/bin/env python3
"""1С OData Telegram Bot — роутер агентов.

Загружает конфигурацию, инициализирует агентов и маршрутизирует
сообщения Telegram к соответствующему агенту.
"""

from __future__ import annotations

import asyncio
import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

# Обеспечить, что корень проекта в sys.path (для запуска python bot/bot.py)
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest
from telegram.error import BadRequest, TimedOut

from bot.agents.base import BaseAgent
from bot.agents.odata import ODataAgent
from bot.agents.formatter import FormatterAgent
from bot.utils import RateLimiter, load_config, sanitize_telegram_html

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_cfg: dict[str, Any] = {}
_agents: dict[str, BaseAgent] = {}  # name → agent instance
_history: dict[int, list[dict[str, str]]] = {}  # chat_id → messages
_cache_dir: str = ".cache"
_env_file: str = "env.json"

AGENT_REGISTRY: dict[str, type[BaseAgent]] = {
    "odata": ODataAgent,
    "formatter": FormatterAgent,
    # Будущие агенты добавляются сюда:
    # "accounting": AccountingAgent,
    # "reports": ReportsAgent,
}

# Ссылка на форматтер (инициализируется автоматически)
_formatter: FormatterAgent | None = None


# ---------------------------------------------------------------------------
# Agent lifecycle
# ---------------------------------------------------------------------------

async def init_agents(profile_cfg: dict[str, Any], cache_dir: str, env_file: str) -> None:
    """Инициализация всех настроенных агентов."""
    global _agents, _formatter

    agents_config = profile_cfg.get("agents", {})
    if not agents_config:
        log.warning("Секция 'agents' не найдена в конфигурации — агенты не загружены")
        return

    # Общие настройки, которые передаются каждому агенту
    global_config = {
        "ai_api_key": profile_cfg.get("ai_api_key", ""),
        "ai_base_url": profile_cfg.get("ai_base_url"),
        "ai_model": profile_cfg.get("ai_model", "gpt-4o-mini"),
        "ai_rpm": profile_cfg.get("ai_rpm", 20),
    }

    for agent_name, agent_cfg in agents_config.items():
        agent_type_name = agent_cfg.get("type", agent_name)
        agent_cls = AGENT_REGISTRY.get(agent_type_name)
        if not agent_cls:
            log.warning("Неизвестный тип агента: '%s' (пропуск)", agent_type_name)
            continue

        log.info("Инициализация агента '%s' (тип: %s)...", agent_name, agent_type_name)
        agent = agent_cls()
        try:
            await agent.initialize(
                agent_config=agent_cfg,
                global_config=global_config,
                cache_dir=cache_dir,
                env_file=env_file,
            )
            _agents[agent_name] = agent
            log.info("Агент '%s' готов", agent_name)
        except Exception as e:
            log.error("Ошибка инициализации агента '%s': %s", agent_name, e)

    # Авто-инициализация форматтера, если он не задан в конфигурации явно
    if "formatter" not in _agents:
        formatter_cfg = profile_cfg.get("formatter", {})
        formatter = FormatterAgent()
        try:
            await formatter.initialize(
                agent_config=formatter_cfg,
                global_config=global_config,
                cache_dir=cache_dir,
                env_file=env_file,
            )
            _formatter = formatter
            log.info("FormatterAgent автоматически инициализирован (не в agents)")
        except Exception as e:
            log.warning("Не удалось инициализировать FormatterAgent: %s", e)
    else:
        _formatter = _agents["formatter"]  # type: ignore[assignment]


async def shutdown_agents() -> None:
    """Корректное завершение всех агентов."""
    for name, agent in _agents.items():
        try:
            await agent.shutdown()
            log.info("Агент '%s' остановлен", name)
        except Exception as e:
            log.error("Ошибка остановки агента '%s': %s", name, e)
    _agents.clear()


# ---------------------------------------------------------------------------
# Default agent (for routing)
# ---------------------------------------------------------------------------

def _default_agent() -> BaseAgent | None:
    """Вернуть агент по умолчанию (первый odata, или просто первый)."""
    if "odata" in _agents:
        return _agents["odata"]
    if _agents:
        return next(iter(_agents.values()))
    return None


# ---------------------------------------------------------------------------
# Telegram handlers
# ---------------------------------------------------------------------------

async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /start."""
    agent_names = ", ".join(_agents.keys()) or "(нет)"
    lines = [
        "🤖 <b>Бот для работы с 1С</b>",
        "",
        f"Подключённые агенты: {agent_names}",
        "",
        "Просто напишите запрос, и я постараюсь помочь.",
        "",
        "/refresh — обновить метаданные 1С",
        "/status — статус агентов",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /status — показать статус всех агентов."""
    if not _agents:
        await update.message.reply_text("⚠️ Нет подключённых агентов.")
        return

    lines = ["📊 <b>Статус агентов</b>\n"]
    for name, agent in _agents.items():
        status = agent.get_status()
        status_icon = "✅" if status.get("initialized") else "❌"
        lines.append(f"{status_icon} <b>{name}</b>")
        for k, v in status.items():
            if k not in ("name", "initialized"):
                lines.append(f"   {k}: <code>{v}</code>")
        lines.append("")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def handle_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /refresh — обновить данные всех агентов."""
    if not _agents:
        await update.message.reply_text("⚠️ Нет подключённых агентов.")
        return

    results: list[str] = []
    for name, agent in _agents.items():
        try:
            await agent.refresh()
            results.append(f"✅ {name}")
        except Exception as e:
            results.append(f"❌ {name}: {e}")

    await update.message.reply_text(
        "🔄 <b>Обновление агентов</b>\n\n" + "\n".join(results),
        parse_mode="HTML",
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка текстового сообщения — маршрутизация агенту."""
    if not update.message or not update.message.text:
        return

    user_text = update.message.text.strip()
    chat_id = update.effective_chat.id

    # Выбрать агента
    agent = _default_agent()
    if not agent:
        await update.message.reply_text("⚠️ Нет доступных агентов для обработки запроса.")
        return

    # Получить историю
    history = _history.get(chat_id, [])

    # Обработка основным агентом
    answer, updated_history = await agent.process_message(user_text, history)

    # Сохранить историю
    _history[chat_id] = updated_history

    # Форматирование через FormatterAgent (если доступен)
    if _formatter and _formatter.is_initialized:
        try:
            answer = await _formatter.format_response(answer, user_question=user_text)
        except Exception as e:
            log.warning("FormatterAgent: ошибка форматирования (%s), отправляю как есть", e)

    # Truncate (Telegram limit: 4096 chars)
    if len(answer) > 4000:
        answer = answer[:4000] + "... (сообщение сокращено)"

    # Санитизация HTML перед отправкой
    safe_answer = sanitize_telegram_html(answer)

    # Отправить: сначала с HTML, при BadRequest — plain text fallback
    try:
        await update.message.reply_text(safe_answer, parse_mode="HTML")
    except BadRequest as e:
        log.warning("Telegram BadRequest при HTML-отправке: %s. Отправляю plain text.", e)
        try:
            # Убрать все HTML-теги для plain-text варианта
            import re
            plain = re.sub(r"<[^>]+>", "", safe_answer)
            if len(plain) > 4000:
                plain = plain[:4000] + "... (сообщение сокращено)"
            await update.message.reply_text(plain)
        except Exception:
            log.error("Telegram не удалось отправить даже plain text")
    except TimedOut:
        # Ретри при таймауте
        sent = False
        for attempt in range(2):
            log.warning("Telegram reply_text TimedOut, retry %d/2", attempt + 1)
            await asyncio.sleep(2)
            try:
                await update.message.reply_text(safe_answer, parse_mode="HTML")
                sent = True
                break
            except TimedOut:
                continue
            except BadRequest:
                break
        if not sent:
            log.error("Telegram reply_text failed after retries (TimedOut)")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("PTB error", exc_info=context.error)


# ---------------------------------------------------------------------------
# Lifecycle hooks
# ---------------------------------------------------------------------------

async def post_init(application) -> None:
    """Called after the Telegram app is fully initialized."""
    global _cfg

    profile_cfg = _cfg
    await init_agents(profile_cfg, _cache_dir, _env_file)

    # Log status
    if _agents:
        log.info("Агентов загружено: %d (%s)", len(_agents), ", ".join(_agents.keys()))
    else:
        log.error("Ни один агент не был загружен")


async def post_shutdown(application) -> None:
    """Called when the Telegram app is shutting down."""
    await shutdown_agents()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global _cfg, _cache_dir, _env_file

    _ROOT = Path(__file__).parent.parent
    parser = argparse.ArgumentParser(description="1С Telegram Bot (Multi-Agent)")
    parser.add_argument("--env-file", default=str(_ROOT / "env.json"))
    parser.add_argument("--profile", default="default")
    parser.add_argument("--cache-dir", default=str(_ROOT / ".cache"))
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level, logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    _env_file = args.env_file
    _cache_dir = args.cache_dir

    _cfg = load_config(args.env_file, args.profile)

    # Увеличенные таймауты для Telegram API (default ~10s слишком мало при долгой обработке)
    request = HTTPXRequest(connect_timeout=30, read_timeout=120, write_timeout=60)

    app = (
        ApplicationBuilder()
        .token(_cfg["telegram_token"])
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .request(request)
        .build()
    )
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("status", handle_status))
    app.add_handler(CommandHandler("refresh", handle_refresh))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    log.info("Бот запущен (multi-agent). Нажмите Ctrl+C для остановки.")
    # Рестарт при сетевых ошибках (ConnectTimeout, TimedOut)
    while True:
        try:
            app.run_polling(drop_pending_updates=True, close_loop=False)
        except (TimedOut, TimeoutError) as e:
            log.warning("Polling error (restart): %s", e)
            import time
            time.sleep(5)
            continue
        break


if __name__ == "__main__":
    main()