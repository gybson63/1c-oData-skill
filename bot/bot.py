#!/usr/bin/env python3
"""1С OData Telegram Bot — роутер агентов.

Загружает конфигурацию, инициализирует агентов и маршрутизирует
сообщения Telegram к соответствующему агенту.

Архитектура:
  ChatManager → Chat → Agent (process_message) → FormatterAgent → Telegram
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any

# Обеспечить, что корень проекта в sys.path (для запуска python bot/bot.py)
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from telegram import Message, Update  # noqa: E402
from telegram.error import BadRequest, NetworkError, TimedOut  # noqa: E402
from telegram.ext import (  # noqa: E402
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.agents.analyst import AnalystAgent  # noqa: E402
from bot.agents.base import BaseAgent  # noqa: E402
from bot.agents.formatter import FormatterAgent  # noqa: E402
from bot.agents.odata import ODataAgent  # noqa: E402
from bot.chat import ChatManager, PaginationError  # noqa: E402
from bot.config import build_global_config, get_settings, load_settings  # noqa: E402
from bot.history import HistoryManager  # noqa: E402
from bot.logging_config import setup_logging  # noqa: E402
from bot.metrics import (  # noqa: E402
    metrics as app_metrics,
)
from bot.metrics import (  # noqa: E402
    session_tokens,
    setup_cost_logging,
    setup_provider_response_logging,
)
from bot.response_error_journal import setup_error_response_journal  # noqa: E402
from bot.telegram_transport import LoggingHTTPXRequest  # noqa: E402
from bot_lib.exceptions import AIError, ODataError, ODataSkillError  # noqa: E402

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_chat_mgr: ChatManager | None = None  # единая точка входа для работы с чатами

AGENT_REGISTRY: dict[str, type[BaseAgent]] = {
    "odata": ODataAgent,
    "analyst": AnalystAgent,
    "formatter": FormatterAgent,
}


# ---------------------------------------------------------------------------
# Agent lifecycle
# ---------------------------------------------------------------------------


async def init_agents(profile_cfg: dict[str, Any], cache_dir: str, env_file: str) -> None:
    """Инициализация всех настроенных агентов + создание ChatManager."""
    global _chat_mgr

    agents: dict[str, BaseAgent] = {}
    formatter: FormatterAgent | None = None

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
        "profile_config": profile_cfg,
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
            agents[agent_name] = agent
            log.info("Агент '%s' готов", agent_name)
        except Exception as e:
            log.error("Ошибка инициализации агента '%s': %s", agent_name, e)

    # Связать Analyst → OData pre-step
    analyst = agents.get("analyst")
    odata = agents.get("odata")
    if analyst and odata and hasattr(analyst, "service") and hasattr(odata, "set_analyst_service"):
        analyst_cfg = agents_config.get("analyst", {})
        from bot.config import parse_analyst_settings

        if parse_analyst_settings(analyst_cfg).preprocessor_for_odata and analyst.service:
            odata.set_analyst_service(analyst.service)
            log.info("ODataAgent: подключён AnalystService (pre-step)")

    # Авто-инициализация форматтера
    if "formatter" not in agents:
        formatter_cfg = profile_cfg.get("formatter", {})
        fmt = FormatterAgent()
        try:
            await fmt.initialize(
                agent_config=formatter_cfg,
                global_config=global_config,
                cache_dir=cache_dir,
                env_file=env_file,
            )
            formatter = fmt
            log.info("FormatterAgent автоматически инициализирован (не в agents)")
        except Exception as e:
            log.warning("Не удалось инициализировать FormatterAgent: %s", e)
    else:
        formatter = agents["formatter"]  # type: ignore[assignment]

    # Создать HistoryManager
    settings = get_settings()
    hs = settings.history
    history_mgr = HistoryManager(
        max_messages=hs.max_messages,
        trim_to=hs.trim_to,
        persist_dir=hs.persist_dir,
    )
    log.info(
        "HistoryManager: max_messages=%d, trim_to=%d, persist_dir=%s",
        hs.max_messages,
        hs.trim_to,
        hs.persist_dir or "(in-memory)",
    )

    # Создать ChatManager
    _chat_mgr = ChatManager(
        agents=agents,
        formatter=formatter,
        history_mgr=history_mgr,
    )

    if agents:
        log.info("Агентов загружено: %d (%s)", len(agents), ", ".join(agents.keys()))
    else:
        log.error("Ни один агент не был загружен")


async def shutdown_agents() -> None:
    """Корректное завершение всех агентов."""
    if not _chat_mgr:
        return
    for name, agent in _chat_mgr.agents.items():
        try:
            await agent.shutdown()
            log.info("Агент '%s' остановлен", name)
        except Exception as e:
            log.error("Ошибка остановки агента '%s': %s", name, e)


# ---------------------------------------------------------------------------
# Telegram send helper
# ---------------------------------------------------------------------------


def _telegram_message(update: Update) -> Message | None:
    """Вернуть message из update или None для неподдерживаемых типов."""
    return update.message


async def _send_telegram_reply(
    update: Update,
    text: str,
    reply_markup=None,
    attachments: list | None = None,
) -> None:
    """Отправить ответ в Telegram с fallback-обработкой ошибок."""
    message = _telegram_message(update)
    if message is None:
        log.warning("Cannot send Telegram reply: update has no message")
        return

    settings = get_settings()
    max_len = settings.telegram.message_max_length

    photo_attachments = [att for att in (attachments or []) if att.content_type.startswith("image/")]

    if photo_attachments:
        photo = photo_attachments[0]
        caption = text
        caption_max = 1024
        if len(caption) > caption_max:
            caption = caption[:caption_max] + "... (сокращено)"
        try:
            await message.reply_photo(
                photo=photo.data,
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        except BadRequest as e:
            log.warning("Telegram BadRequest при отправке photo: %s", e)
            await message.reply_photo(photo=photo.data, reply_markup=reply_markup)
        if len(text) > caption_max:
            remainder = text[caption_max:]
            if len(remainder) > max_len:
                remainder = remainder[:max_len] + "... (сообщение сокращено)"
            try:
                await message.reply_text(remainder, parse_mode="HTML")
            except BadRequest:
                plain = re.sub(r"<[^>]+>", "", remainder)
                await message.reply_text(plain)
        return

    try:
        await message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except BadRequest as e:
        log.warning("Telegram BadRequest при HTML-отправке: %s. Отправляю plain text.", e)
        try:
            plain = re.sub(r"<[^>]+>", "", text)
            if len(plain) > max_len:
                plain = plain[:max_len] + "... (сообщение сокращено)"
            await message.reply_text(plain, reply_markup=reply_markup)
        except Exception:
            log.error("Telegram не удалось отправить даже plain text")
    except TimedOut:
        tg_settings = get_settings().telegram
        sent = False
        for attempt in range(tg_settings.retry_count):
            log.warning("Telegram reply_text TimedOut, retry %d/%d", attempt + 1, tg_settings.retry_count)
            await asyncio.sleep(tg_settings.retry_delay)
            try:
                await message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
                sent = True
                break
            except TimedOut:
                continue
            except BadRequest:
                break
        if not sent:
            log.error("Telegram reply_text failed after retries (TimedOut)")


# ---------------------------------------------------------------------------
# Telegram handlers
# ---------------------------------------------------------------------------


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /start."""
    message = _telegram_message(update)
    if message is None:
        return

    agents = _chat_mgr.agents if _chat_mgr else {}
    agent_names = ", ".join(agents.keys()) or "(нет)"
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
        "/tokens — расход токенов текущей сессии",
        "/clear — очистить историю диалога",
        "/history — статистика истории",
        "/analyze — анализ объектов метаданных (без OData-запроса)",
    ]
    await message.reply_text("\n".join(lines), parse_mode="HTML")


async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /status — показать статус всех агентов."""
    message = _telegram_message(update)
    if message is None:
        return

    if not _chat_mgr:
        await message.reply_text("⚠️ ChatManager не инициализирован.")
        return

    agents = _chat_mgr.agents
    if not agents:
        await message.reply_text("⚠️ Нет подключённых агентов.")
        return

    lines = ["📊 <b>Статус агентов</b>\n"]
    for name, agent in agents.items():
        status = agent.get_status()
        status_icon = "✅" if status.get("initialized") else "❌"
        lines.append(f"{status_icon} <b>{name}</b>")
        for k, v in status.items():
            if k not in ("name", "initialized"):
                lines.append(f"   {k}: <code>{v}</code>")
        lines.append("")

    await message.reply_text("\n".join(lines), parse_mode="HTML")


async def handle_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /clear — очистить историю диалога."""
    message = _telegram_message(update)
    if message is None:
        return

    if not _chat_mgr:
        await message.reply_text("⚠️ ChatManager не инициализирован.")
        return

    chat = update.effective_chat
    if chat is None:
        return

    chat_id = chat.id
    chat_obj = _chat_mgr.get_or_create(chat_id)
    chat_obj.clear()

    await message.reply_text("🗑 История диалога очищена.")


async def handle_history_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /history — показать статистику истории."""
    message = _telegram_message(update)
    if message is None:
        return

    if not _chat_mgr:
        await message.reply_text("⚠️ ChatManager не инициализирован.")
        return

    chat = update.effective_chat
    if chat is None:
        return

    chat_id = chat.id
    history_mgr = _chat_mgr.history_mgr
    history = history_mgr.get(chat_id)
    total_chats = history_mgr.chat_count()
    total_msgs = history_mgr.total_messages()

    lines = [
        "📜 <b>Статистика истории</b>",
        "",
        f"Сообщений в этом чате: <b>{len(history)}</b>",
        f"Всего чатов с историей: <b>{total_chats}</b>",
        f"Всего сообщений: <b>{total_msgs}</b>",
        "",
        f"Лимит сообщений на чат: {history_mgr.max_messages}",
        f"Обрезка до: {history_mgr.trim_to}",
        f"Персистентность: {'✅ да' if history_mgr.is_persistent else '❌ нет'}",
    ]
    await message.reply_text("\n".join(lines), parse_mode="HTML")


async def handle_metrics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /metrics — показать метрики производительности и AI-usage."""
    message = _telegram_message(update)
    if message is None:
        return

    report = app_metrics.format_report()
    await message.reply_text(report, parse_mode="HTML")


async def handle_tokens(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /tokens — показать расход токенов текущей сессии."""
    message = _telegram_message(update)
    if message is None:
        return

    chat = update.effective_chat
    if chat is None:
        return

    chat_id = chat.id
    report = session_tokens.format_session_report(chat_id)
    await message.reply_text(report, parse_mode="HTML")


async def handle_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /refresh — обновить данные всех агентов."""
    message = _telegram_message(update)
    if message is None:
        return

    if not _chat_mgr:
        await message.reply_text("⚠️ ChatManager не инициализирован.")
        return

    agents = _chat_mgr.agents
    if not agents:
        await message.reply_text("⚠️ Нет подключённых агентов.")
        return

    results: list[str] = []
    for name, agent in agents.items():
        try:
            await agent.refresh()
            results.append(f"✅ {name}")
        except Exception as e:
            results.append(f"❌ {name}: {e}")

    await message.reply_text(
        "🔄 <b>Обновление агентов</b>\n\n" + "\n".join(results),
        parse_mode="HTML",
    )


async def handle_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /analyze — standalone анализ метаданных."""
    message = _telegram_message(update)
    if message is None:
        return

    if not _chat_mgr:
        await message.reply_text("⚠️ ChatManager не инициализирован.")
        return

    question = " ".join(context.args) if context.args else ""
    if not question.strip():
        await message.reply_text(
            "Использование: <code>/analyze ваш вопрос</code>\n"
            "Пример: <code>/analyze Сколько дней отпуска осталось у сотрудников?</code>",
            parse_mode="HTML",
        )
        return

    if "analyst" not in _chat_mgr.agents:
        await message.reply_text("⚠️ Агент analyst не настроен в env.json.")
        return

    chat = update.effective_chat
    if chat is None:
        return

    chat_id = chat.id
    chat_obj = _chat_mgr.get_or_create(chat_id)

    try:
        response = await chat_obj.process_analyze(question.strip())
    except Exception as e:
        log.exception("Analyze error in chat %s", chat_id)
        await message.reply_text(f"⚠️ Ошибка анализа: {e}")
        return

    await _send_telegram_reply(update, response.text, response.reply_markup, attachments=response.attachments)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка текстового сообщения — маршрутизация через ChatManager."""
    message = _telegram_message(update)
    if message is None or not message.text:
        return

    if not _chat_mgr:
        log.error("ChatManager не инициализирован")
        await message.reply_text("⚠️ Внутренняя ошибка: бот не готов.")
        return

    user_text = message.text.strip()
    chat = update.effective_chat
    if chat is None:
        return

    chat_id = chat.id

    chat_obj = _chat_mgr.get_or_create(chat_id)

    # Обработка через Chat (пайплайн: агент → форматирование → обрезка → пагинация)
    try:
        response = await chat_obj.process_message(user_text)
    except ODataError as e:
        log.error("OData error in chat %s: %s", chat_id, e)
        await message.reply_text(f"⚠️ Ошибка OData: {e}")
        return
    except AIError as e:
        log.error("AI error in chat %s: %s", chat_id, e)
        await message.reply_text(f"⚠️ Ошибка AI: {e}")
        return
    except ODataSkillError as e:
        log.error("Internal error in chat %s: %s", chat_id, e)
        await message.reply_text(f"⚠️ Внутренняя ошибка: {e}")
        return
    except Exception as e:
        log.exception("Unexpected error in chat %s", chat_id)
        await message.reply_text(f"⚠️ Непредвиденная ошибка: {e}")
        return

    # Только отправка в Telegram (transport layer)
    await _send_telegram_reply(
        update,
        response.text,
        response.reply_markup,
        attachments=response.attachments,
    )


async def handle_pagination_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка inline-кнопок пагинации (callback query)."""
    query = update.callback_query
    if query is None:
        return

    await query.answer()

    # Мгновенная обратная связь — показать «Загрузка»
    try:
        await query.edit_message_text("⏳ <i>Загрузка данных...</i>", parse_mode="HTML")
    except Exception:
        pass  # сообщение могло уже измениться — не критично

    if not _chat_mgr:
        await query.edit_message_text("⚠️ ChatManager не инициализирован.")
        return

    chat = update.effective_chat
    if chat is None:
        return

    chat_id = chat.id
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

    # Обработка через Chat (тот же пайплайн, что и для обычного сообщения)
    chat_obj = _chat_mgr.get_or_create(chat_id)
    try:
        response = await chat_obj.process_pagination(skip)
    except PaginationError as e:
        await query.edit_message_text(f"⚠️ {e}")
        return
    except Exception as e:
        log.exception("Pagination error in chat %s", chat_id)
        await query.edit_message_text(f"⚠️ Ошибка: {e}")
        return

    try:
        await query.edit_message_text(response.text, parse_mode="HTML", reply_markup=response.reply_markup)
    except BadRequest as e:
        log.warning("Pagination edit BadRequest: %s. Sending new message.", e)
        callback_message = query.message
        if isinstance(callback_message, Message):
            try:
                await callback_message.reply_text(response.text, parse_mode="HTML", reply_markup=response.reply_markup)
            except Exception:
                log.error("Pagination: не удалось отправить сообщение")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    extra: dict[str, Any] = {"error_type": type(error).__name__ if error else "Unknown"}
    if isinstance(update, Update):
        if update.effective_chat:
            extra["chat_id"] = update.effective_chat.id
        if update.message:
            extra["update_type"] = "message"
        elif update.callback_query:
            extra["update_type"] = "callback_query"
        elif update.edited_message:
            extra["update_type"] = "edited_message"
        else:
            extra["update_type"] = "other"
    log.error("PTB error: %s", error, extra=extra, exc_info=error)


# ---------------------------------------------------------------------------
# Lifecycle hooks
# ---------------------------------------------------------------------------


async def _announce_telegram_ready(application) -> None:
    """Сообщить о готовности после старта polling и Application.start()."""
    if not _chat_mgr or not _chat_mgr.agents:
        log.error("Бот не запущен: агенты не инициализированы")
        return

    while not application.running:
        await asyncio.sleep(0.05)

    log.info("Бот приступил к работе")


async def post_init(application) -> None:
    """Called after the Telegram app is fully initialized."""
    settings = get_settings()

    # Собираем legacy-совместимый dict для init_agents
    profile_cfg: dict[str, Any] = {
        "agents": settings.agents_config,
        "formatter": settings.formatter.model_dump(),
        **build_global_config(settings),
    }

    await init_agents(profile_cfg, settings.cache_dir, "env.json")
    application.create_task(_announce_telegram_ready(application))


async def post_shutdown(application) -> None:
    """Called when the Telegram app is shutting down."""
    await shutdown_agents()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    _root = Path(__file__).parent.parent
    parser = argparse.ArgumentParser(description="1С Telegram Bot (Multi-Agent)")
    parser.add_argument("--env-file", default=str(_root / "env.json"))
    parser.add_argument("--profile", default="default")
    parser.add_argument("--cache-dir", default=str(_root / ".cache"))
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Уровень логирования (CLI имеет приоритет над конфигом)",
    )
    parser.add_argument("--log-file", default=None, help="Путь к файлу лога (поворот 5 МБ)")
    args = parser.parse_args()

    # Загрузить типизированную конфигурацию через Pydantic Settings
    settings = load_settings(env_file=args.env_file, profile=args.profile)

    # Настроить логирование (CLI имеет приоритет над конфигом)
    log_level = args.log_level or settings.log_level or "INFO"
    log_file = args.log_file or settings.log_file
    setup_logging(level=log_level, log_file=log_file)

    # Инициализировать логирование AI-затрат в logs/costs/
    setup_cost_logging(cost_dir="logs/costs")

    # Инициализировать сохранение ответов провайдера в logs/<session_id>/
    setup_provider_response_logging(log_dir="logs")
    setup_error_response_journal(log_dir="logs")
    from bot.agents.odata.parse_failure import setup_parse_failure_journal

    setup_parse_failure_journal(log_dir="logs")

    tg = settings.telegram

    request_kwargs: dict[str, Any] = {
        "connect_timeout": tg.connect_timeout,
        "read_timeout": tg.read_timeout,
        "write_timeout": tg.write_timeout,
    }
    if tg.proxy_url:
        request_kwargs["proxy"] = tg.proxy_url
    if tg.use_env_proxy:
        request_kwargs["httpx_kwargs"] = {"trust_env": True}
    elif tg.proxy_url:
        request_kwargs["httpx_kwargs"] = {"trust_env": False}

    request = LoggingHTTPXRequest(**request_kwargs)

    builder = (
        ApplicationBuilder()
        .token(settings.bot.token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .request(request)
    )
    if tg.base_url:
        builder = builder.base_url(tg.base_url)
    if tg.base_file_url:
        builder = builder.base_file_url(tg.base_file_url)
    app = builder.build()
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("status", handle_status))
    app.add_handler(CommandHandler("refresh", handle_refresh))
    app.add_handler(CommandHandler("metrics", handle_metrics))
    app.add_handler(CommandHandler("clear", handle_clear))
    app.add_handler(CommandHandler("tokens", handle_tokens))
    app.add_handler(CommandHandler("history", handle_history_stats))
    app.add_handler(CommandHandler("analyze", handle_analyze))
    app.add_handler(CallbackQueryHandler(handle_pagination_callback, pattern=r"^page:\d+$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    log.info("Инициализация Telegram-бота… Нажмите Ctrl+C для остановки.")
    if tg.proxy_url:
        log.info("Telegram proxy: %s", tg.proxy_url)
    elif tg.use_env_proxy:
        log.info("Telegram: используются системные HTTP(S)_PROXY из окружения")
    # Рестарт при сетевых ошибках (ConnectTimeout, TimedOut, NetworkError)
    while True:
        try:
            app.run_polling(drop_pending_updates=True, close_loop=False)
        except (TimedOut, TimeoutError, NetworkError) as e:
            log.warning(
                "polling_network_error (restart через %ss): %s",
                tg.polling_restart_delay,
                e,
                extra={"error_type": type(e).__name__},
            )
            if "ConnectError" in str(e):
                log.warning(
                    "Нет доступа к api.telegram.org. Включите VPN/прокси или укажите "
                    "telegram.proxy_url в env.json (например http://127.0.0.1:7890)."
                )
            time.sleep(tg.polling_restart_delay)
            continue
        break


if __name__ == "__main__":
    main()
