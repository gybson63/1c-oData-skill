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
import re
import sys
from pathlib import Path
from typing import Any

# Обеспечить, что корень проекта в sys.path (для запуска python bot/bot.py)
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
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
from bot.config import load_settings, get_settings, build_global_config
from bot.history import HistoryManager
from bot.logging_config import setup_logging
from bot.metrics import metrics as app_metrics
from bot.utils import RateLimiter, sanitize_telegram_html
from bot_lib.exceptions import ODataSkillError, ODataError, AIError, ConfigError

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_agents: dict[str, BaseAgent] = {}  # name → agent instance
_history_mgr: HistoryManager | None = None  # управляет историей чатов

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
# Pagination helpers
# ---------------------------------------------------------------------------

def _extract_pagination_context(history: list[dict]) -> dict | None:
    """Извлечь контекст пагинации из **последнего** assistant-сообщения в истории.

    Проверяем только самое последнее сообщение — иначе при ошибочном ответе
    (не JSON) функция найдёт pagination-контекст от предыдущего успешного
    запроса и покажет кнопку «Следующие» на ошибке.
    """
    if not history:
        return None
    # Берём только последнее assistant-сообщение
    for msg in reversed(history):
        if msg.get("role") == "assistant":
            content = msg.get("content", "")
            try:
                data = json.loads(content)
                if isinstance(data, dict) and "entity" in data:
                    return data
            except (json.JSONDecodeError, TypeError):
                pass
            break  # проверяем только последнее — не идём дальше в историю
    return None


def _build_pagination_keyboard(pagination_ctx: dict | None) -> InlineKeyboardMarkup | None:
    """Построить inline-клавиатуру для пагинации, если есть ещё записи."""
    if not pagination_ctx:
        return None
    total = pagination_ctx.get("total", 0)
    skip = pagination_ctx.get("skip", 0)
    shown = pagination_ctx.get("shown", 0)
    if skip + shown < total:
        top = pagination_ctx.get("top", 20)
        next_skip = skip + top
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("➡️ Следующие", callback_data=f"page:{next_skip}")],
        ])
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
        "/metrics — метрики производительности и AI-usage",
        "/clear — очистить историю диалога",
        "/history — статистика истории",
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


async def handle_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /clear — очистить историю диалога."""
    if not _history_mgr:
        await update.message.reply_text("⚠️ Менеджер истории не инициализирован.")
        return

    chat_id = update.effective_chat.id
    _history_mgr.clear(chat_id)

    # Сбросить контекст пагинации
    agent = _agents.get("odata")
    if agent and isinstance(agent, ODataAgent):
        agent.clear_pagination_state(chat_id)

    await update.message.reply_text("🗑 История диалога очищена.")


async def handle_history_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /history — показать статистику истории."""
    if not _history_mgr:
        await update.message.reply_text("⚠️ Менеджер истории не инициализирован.")
        return

    chat_id = update.effective_chat.id
    history = _history_mgr.get(chat_id)
    total_chats = _history_mgr.chat_count()
    total_msgs = _history_mgr.total_messages()

    lines = [
        "📜 <b>Статистика истории</b>",
        "",
        f"Сообщений в этом чате: <b>{len(history)}</b>",
        f"Всего чатов с историей: <b>{total_chats}</b>",
        f"Всего сообщений: <b>{total_msgs}</b>",
        "",
        f"Лимит сообщений на чат: {_history_mgr._max}",
        f"Обрезка до: {_history_mgr._trim_to}",
        f"Персистентность: {'✅ да' if _history_mgr._persist_dir else '❌ нет'}",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def handle_metrics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /metrics — показать метрики производительности и AI-usage."""
    report = app_metrics.format_report()
    await update.message.reply_text(report, parse_mode="HTML")


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

    # Получить историю через HistoryManager
    if not _history_mgr:
        log.error("HistoryManager не инициализирован")
        await update.message.reply_text("⚠️ Внутренняя ошибка: менеджер истории не готов.")
        return
    history = _history_mgr.get(chat_id)

    # Обработка основным агентом
    try:
        answer, updated_history = await agent.process_message(user_text, history)
    except ODataError as e:
        log.error("OData error in chat %s: %s", chat_id, e)
        await update.message.reply_text(f"⚠️ Ошибка OData: {e}")
        return
    except AIError as e:
        log.error("AI error in chat %s: %s", chat_id, e)
        await update.message.reply_text(f"⚠️ Ошибка AI: {e}")
        return
    except ODataSkillError as e:
        log.error("Internal error in chat %s: %s", chat_id, e)
        await update.message.reply_text(f"⚠️ Внутренняя ошибка: {e}")
        return
    except Exception as e:
        log.exception("Unexpected error in chat %s", chat_id)
        await update.message.reply_text(f"⚠️ Непредвиденная ошибка: {e}")
        return

    # Сохранить историю через HistoryManager (с автоматической обрезкой и персистентностью)
    _history_mgr.save(chat_id, updated_history)

    # Форматирование через FormatterAgent (если доступен)
    if _formatter and _formatter.is_initialized:
        try:
            answer = await _formatter.format_response(answer, user_question=user_text)
        except Exception as e:
            log.warning("FormatterAgent: ошибка форматирования (%s), отправляю как есть", e)

    # Truncate (Telegram limit: 4096 chars)
    settings = get_settings()
    max_len = settings.telegram.message_max_length
    if len(answer) > max_len:
        answer = answer[:max_len] + "... (сообщение сокращено)"

    # Санитизация HTML перед отправкой
    safe_answer = sanitize_telegram_html(answer)

    # Пагинация: проверить, есть ли ещё записи для показа
    reply_markup = None
    if isinstance(agent, ODataAgent):
        pagination_ctx = _extract_pagination_context(updated_history)
        if pagination_ctx:
            agent.save_pagination_state(chat_id, pagination_ctx)
            reply_markup = _build_pagination_keyboard(pagination_ctx)

    # Отправить: сначала с HTML, при BadRequest — plain text fallback
    try:
        await update.message.reply_text(safe_answer, parse_mode="HTML", reply_markup=reply_markup)
    except BadRequest as e:
        log.warning("Telegram BadRequest при HTML-отправке: %s. Отправляю plain text.", e)
        try:
            plain = re.sub(r"<[^>]+>", "", safe_answer)
            if len(plain) > max_len:
                plain = plain[:max_len] + "... (сообщение сокращено)"
            await update.message.reply_text(plain, reply_markup=reply_markup)
        except Exception:
            log.error("Telegram не удалось отправить даже plain text")
    except TimedOut:
        tg_settings = get_settings().telegram
        sent = False
        for attempt in range(tg_settings.retry_count):
            log.warning("Telegram reply_text TimedOut, retry %d/%d", attempt + 1, tg_settings.retry_count)
            await asyncio.sleep(tg_settings.retry_delay)
            try:
                await update.message.reply_text(safe_answer, parse_mode="HTML", reply_markup=reply_markup)
                sent = True
                break
            except TimedOut:
                continue
            except BadRequest:
                break
        if not sent:
            log.error("Telegram reply_text failed after retries (TimedOut)")


async def handle_pagination_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка inline-кнопок пагинации (callback query)."""
    query = update.callback_query
    await query.answer()

    # Мгновенная обратная связь — показать «Загрузка» до завершения запроса
    try:
        await query.edit_message_text("⏳ <i>Загрузка данных...</i>", parse_mode="HTML")
    except Exception:
        pass  # сообщение могло уже измениться — не критично

    chat_id = update.effective_chat.id
    data = query.data or ""

    # Разбор callback_data: "page:<skip>"
    if not data.startswith("page:"):
        await query.answer("Неизвестное действие", show_alert=True)
        return

    try:
        skip = int(data.split(":")[1])
    except (ValueError, IndexError):
        await query.answer("Ошибка пагинации", show_alert=True)
        return

    # Найти OData-агент
    agent = _agents.get("odata")
    if not agent or not isinstance(agent, ODataAgent):
        await query.edit_message_text("⚠️ Агент OData не доступен.")
        return

    # Выполнить запрос с новым skip
    try:
        answer, pagination_ctx = await agent.execute_page(chat_id, skip)
    except Exception as e:
        log.exception("Pagination error in chat %s", chat_id)
        await query.edit_message_text(f"⚠️ Ошибка: {e}")
        return

    # Форматирование через FormatterAgent
    if _formatter and _formatter.is_initialized:
        try:
            answer = await _formatter.format_response(answer, user_question="продолжение")
        except Exception as e:
            log.warning("FormatterAgent: ошибка при пагинации (%s)", e)

    # Проверить, есть ли ещё страницы
    reply_markup = _build_pagination_keyboard(pagination_ctx)

    # Обновить сообщение
    max_len = get_settings().telegram.message_max_length
    if len(answer) > max_len:
        answer = answer[:max_len] + "... (сообщение сокращено)"

    safe_answer = sanitize_telegram_html(answer)

    try:
        await query.edit_message_text(safe_answer, parse_mode="HTML", reply_markup=reply_markup)
    except BadRequest as e:
        log.warning("Pagination edit BadRequest: %s. Sending new message.", e)
        try:
            await query.message.reply_text(safe_answer, parse_mode="HTML", reply_markup=reply_markup)
        except Exception:
            log.error("Pagination: не удалось отправить сообщение")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("PTB error", exc_info=context.error)


# ---------------------------------------------------------------------------
# Lifecycle hooks
# ---------------------------------------------------------------------------

async def post_init(application) -> None:
    """Called after the Telegram app is fully initialized."""
    settings = get_settings()

    # Собираем legacy-совместимый dict для init_agents
    profile_cfg: dict[str, Any] = {
        "agents": settings.agents_config,
        "formatter": settings.formatter.model_dump(),
        **build_global_config(settings),
    }

    # Инициализировать HistoryManager с настройками
    global _history_mgr
    hs = settings.history
    _history_mgr = HistoryManager(
        max_messages=hs.max_messages,
        trim_to=hs.trim_to,
        persist_dir=hs.persist_dir,
    )
    log.info(
        "HistoryManager: max_messages=%d, trim_to=%d, persist_dir=%s",
        hs.max_messages, hs.trim_to, hs.persist_dir or "(in-memory)",
    )

    await init_agents(profile_cfg, settings.cache_dir, "env.json")

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
    _ROOT = Path(__file__).parent.parent
    parser = argparse.ArgumentParser(description="1С Telegram Bot (Multi-Agent)")
    parser.add_argument("--env-file", default=str(_ROOT / "env.json"))
    parser.add_argument("--profile", default="default")
    parser.add_argument("--cache-dir", default=str(_ROOT / ".cache"))
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-file", default=None, help="Путь к файлу лога (поворот 5 МБ)")
    args = parser.parse_args()

    # Загрузить типизированную конфигурацию через Pydantic Settings
    settings = load_settings(env_file=args.env_file, profile=args.profile)

    # Настроить логирование (используем уровень из конфига, CLI имеет приоритет)
    log_level = args.log_level or settings.log_level
    log_file = args.log_file or settings.log_file
    setup_logging(level=log_level, log_file=log_file)

    tg = settings.telegram

    # Увеличенные таймауты для Telegram API (default ~10s слишком мало при долгой обработке)
    request = HTTPXRequest(
        connect_timeout=tg.connect_timeout,
        read_timeout=tg.read_timeout,
        write_timeout=tg.write_timeout,
    )

    app = (
        ApplicationBuilder()
        .token(settings.bot.token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .request(request)
        .build()
    )
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("status", handle_status))
    app.add_handler(CommandHandler("refresh", handle_refresh))
    app.add_handler(CommandHandler("metrics", handle_metrics))
    app.add_handler(CommandHandler("clear", handle_clear))
    app.add_handler(CommandHandler("history", handle_history_stats))
    app.add_handler(CallbackQueryHandler(handle_pagination_callback, pattern=r"^page:\d+$"))
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
            time.sleep(tg.polling_restart_delay)
            continue
        break


if __name__ == "__main__":
    main()