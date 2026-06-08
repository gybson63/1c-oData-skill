#!/usr/bin/env python3
"""Модуль управления чатами.

Инкапсулирует состояние отдельного чата и пайплайн обработки сообщений:
  история → агент → форматирование → обрезка → пагинация.

Классы:
  - ChatResponse — результат обработки сообщения (текст + клавиатура)
  - Chat — состояние и логика одного чата
  - ChatManager — фабрика/реестр чатов
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, cast

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.agents.base import BaseAgent
from bot.agents.formatter import FormatterAgent
from bot.agents.odata.parse_failure import journal_parse_failure_from_response
from bot.config import get_settings
from bot.email.attachment_builder import prepare_email_response
from bot.history import HistoryManager
from bot.messages import (
    AgentProcessResult,
    Attachment,
    InboundMessage,
    OutboundMessage,
    TransportChannel,
    conversation_id_to_chat_id,
)
from bot.metrics import session_tokens
from bot.response_error_journal import journal_error_response_if_needed
from bot.utils import sanitize_telegram_html

log = logging.getLogger(__name__)


class PaginationError(Exception):
    """Ошибка обработки запроса пагинации, текст которой можно показать пользователю."""


# ---------------------------------------------------------------------------
# ChatResponse
# ---------------------------------------------------------------------------


@dataclass
class ChatResponse:
    """Результат обработки сообщения чатом.

    Attributes:
        text: HTML-ответ, готовый к отправке в Telegram.
        reply_markup: Inline-клавиатура для пагинации (или None).
        raw_answer: Ответ агента до форматирования (для отладки).
        attachments: Вложения (PNG-графики и т.п.).
    """

    text: str
    reply_markup: InlineKeyboardMarkup | None = None
    raw_answer: str = ""
    attachments: list[Attachment] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


class Chat:
    """Инкапсулирует состояние и пайплайн обработки одного чата.

    Содержит:
      - ссылку на агент (роутинг)
      - ссылку на форматтер
      - ссылку на HistoryManager
      - контекст пагинации (ранее хранился в ODataAgent._pagination_states)
    """

    def __init__(
        self,
        chat_id: int,
        agents: dict[str, BaseAgent],
        default_agent: BaseAgent,
        formatter: FormatterAgent | None,
        history_mgr: HistoryManager,
    ) -> None:
        self.chat_id = chat_id
        self._agents = agents
        self._default_agent = default_agent
        self._formatter = formatter
        self._history_mgr = history_mgr

        # Контекст пагинации (ранее ODataAgent._pagination_states[chat_id])
        self._pagination_ctx: dict[str, Any] | None = None

    # -- history -------------------------------------------------------------

    @property
    def history(self) -> list[dict[str, str]]:
        """Текущая история диалога."""
        return self._history_mgr.get(self.chat_id)

    def save_history(self, updated_history: list[dict[str, str]]) -> None:
        """Сохранить обновлённую историю (с автоматической обрезкой)."""
        self._history_mgr.save(self.chat_id, updated_history)

    # -- pagination ----------------------------------------------------------

    @property
    def pagination_ctx(self) -> dict[str, Any] | None:
        """Текущий контекст пагинации."""
        return self._pagination_ctx

    def save_pagination_state(self, ctx: dict[str, Any]) -> None:
        """Сохранить контекст пагинации."""
        self._pagination_ctx = ctx

    def clear_pagination_state(self) -> None:
        """Сбросить контекст пагинации."""
        self._pagination_ctx = None

    @staticmethod
    def _is_analyze_intent(text: str) -> bool:
        low = text.lower()
        markers = (
            "какие объекты метаданных",
            "какой объект метаданных",
            "разбери метаданные",
            "где хранятся данные",
        )
        return any(m in low for m in markers)

    def _resolve_agent_and_text(
        self,
        user_text: str,
        *,
        agent_name: str | None = None,
    ) -> tuple[BaseAgent, str]:
        if agent_name:
            agent = self._agents.get(agent_name) or self._default_agent
            return agent, user_text.strip()

        text = user_text.strip()
        if text.lower().startswith("[analyze]"):
            query = text[9:].strip()
            agent = self._agents.get("analyst") or self._default_agent
            return agent, query or text

        if self._is_analyze_intent(text):
            agent = self._agents.get("analyst") or self._default_agent
            return agent, text

        return self._default_agent, text

    async def _run_agent(
        self,
        user_text: str,
        *,
        chat_id: int | None = None,
        agent_name: str | None = None,
    ) -> AgentProcessResult:
        history = self._history_mgr.get(chat_id if chat_id is not None else self.chat_id)
        agent, query_text = self._resolve_agent_and_text(user_text, agent_name=agent_name)
        return await agent.process_message(
            query_text,
            history,
            chat_id=chat_id if chat_id is not None else self.chat_id,
        )

    # -- core processing -----------------------------------------------------

    async def process_inbound(self, inbound: InboundMessage) -> OutboundMessage:
        """Обработка входящего сообщения из любого транспорта."""
        chat_id = inbound.chat_id
        user_text = inbound.text
        format_question = user_text

        history = self._history_mgr.get(chat_id)

        if inbound.channel == TransportChannel.EMAIL:
            thread_count = inbound.metadata.get("thread_message_count", 1)
            if not history and inbound.thread_context and thread_count > 1:
                user_text = (
                    f"Контекст email-переписки ({thread_count} сообщений):\n\n"
                    f"{inbound.thread_context}\n\n"
                    f"--- Текущий запрос ---\n{inbound.text}"
                )
            format_question = inbound.text

        agent_result = await self._run_agent(user_text, chat_id=chat_id)
        self.save_history(agent_result.history)
        raw_answer = agent_result.text

        if agent_result.skip_formatter:
            answer = agent_result.text
        else:
            answer = await self._format(
                answer=agent_result.text,
                user_question=format_question,
                channel=inbound.channel,
            )

        pagination_ctx = self._extract_pagination_context(agent_result.history)

        if inbound.channel == TransportChannel.EMAIL:
            # Email: без пагинации — загрузить все страницы, если данных больше одной
            if pagination_ctx and self._has_more_pages(pagination_ctx):
                from bot.agents.odata.request_brief_advisor import extract_current_query
                from bot.agents.odata.request_guard import check_request_allowed

                guard = check_request_allowed(extract_current_query(user_text))
                if guard.blocked:
                    log.info("Email fetch-all skipped: %s", guard.reason)
                else:
                    full_answer = await self._fetch_all_for_email(
                        pagination_ctx, user_text, fallback=answer, chat_id=inbound.chat_id
                    )
                    if not agent_result.skip_formatter:
                        answer = await self._format(
                            full_answer,
                            user_question=format_question,
                            channel=inbound.channel,
                        )
                    else:
                        answer = full_answer
            outbound = self._finalize_email(
                answer,
                inbound,
                raw_answer=raw_answer,
                agent_result=agent_result,
            )
            self._journal_error_response(
                outbound.text,
                user_query=format_question,
                inbound=inbound,
                raw_answer=raw_answer,
            )
            return outbound

        if pagination_ctx:
            self.save_pagination_state(pagination_ctx)

        chat_response = self._finalize(
            answer,
            pagination_ctx,
            raw_answer=raw_answer,
            attachments=agent_result.attachments,
        )
        outbound = OutboundMessage(
            text=chat_response.text,
            channel=TransportChannel.TELEGRAM,
            format="html",
            attachments=chat_response.attachments,
            metadata={"reply_markup": chat_response.reply_markup},
        )
        self._journal_error_response(
            outbound.text,
            user_query=format_question,
            inbound=inbound,
            raw_answer=raw_answer,
        )
        return outbound

    async def process_analyze(self, user_text: str) -> ChatResponse:
        """Standalone анализ метаданных через AnalystAgent."""
        agent_result = await self._run_agent(user_text, chat_id=self.chat_id, agent_name="analyst")
        self.save_history(agent_result.history)
        response = self._finalize(
            agent_result.text,
            pagination_ctx=None,
            raw_answer=agent_result.text,
            attachments=agent_result.attachments,
        )
        return response

    async def process_message(self, user_text: str) -> ChatResponse:
        """Полный пайплайн обработки сообщения.

        1. Получить историю
        2. Вызвать агент (process_message)
        3. Сохранить историю
        4. Форматировать через FormatterAgent
        5. Добавить подпись с токенами
        6. Обрезать и санитизировать HTML
        7. Извлечь контекст пагинации
        8. Построить inline-клавиатуру

        Returns:
            ChatResponse с готовым текстом и клавиатурой.

        Raises:
            ODataSkillError, AIError, ODataError — пробрасываются из агента.
        """
        agent_result = await self._run_agent(user_text, chat_id=self.chat_id)

        # Сохранить историю
        self.save_history(agent_result.history)

        raw_answer = agent_result.text

        # Шаг 2: форматирование через FormatterAgent
        if agent_result.skip_formatter:
            answer = agent_result.text
        else:
            answer = await self._format(agent_result.text, user_question=user_text)

        # Шаг 3: пагинация — извлечь и сохранить контекст
        pagination_ctx = self._extract_pagination_context(agent_result.history)
        if pagination_ctx:
            self.save_pagination_state(pagination_ctx)

        # Шаг 4: токены + обрезка + санитизация + клавиатура
        response = self._finalize(
            answer,
            pagination_ctx,
            raw_answer=raw_answer,
            attachments=agent_result.attachments,
        )
        self._journal_error_response(
            response.text,
            user_query=user_text,
            channel=TransportChannel.TELEGRAM,
            chat_id=self.chat_id,
            raw_answer=raw_answer,
        )
        return response

    async def process_pagination(self, skip: int) -> ChatResponse:
        """Пайплайн обработки запроса следующей страницы (callback пагинации).

        Использует сохранённый контекст пагинации, выполняет запрос с новым
        ``skip`` через OData-агент, форматирует и финализирует ответ — той же
        цепочкой, что и :meth:`process_message`.

        Raises:
            PaginationError: контекст потерян или агент недоступен (текст
                ошибки можно показать пользователю).
        """
        ctx = self._pagination_ctx
        if not ctx:
            raise PaginationError("Контекст запроса потерян. Повторите запрос.")

        agent = self._agents.get("odata") or self._default_agent
        if not agent or not hasattr(agent, "execute_page_with_ctx"):
            raise PaginationError("Агент OData не доступен.")

        answer, new_ctx = await agent.execute_page_with_ctx(ctx, skip, chat_id=self.chat_id)

        # Обновить контекст пагинации (None — если страниц больше нет)
        if new_ctx:
            self.save_pagination_state(new_ctx)

        answer = await self._format(answer, user_question="продолжение")
        response = self._finalize(answer, new_ctx)
        self._journal_error_response(
            response.text,
            user_query="продолжение",
            channel=TransportChannel.TELEGRAM,
            chat_id=self.chat_id,
        )
        return response

    # -- pipeline helpers ----------------------------------------------------

    @staticmethod
    def _journal_error_response(
        answer: str,
        *,
        user_query: str,
        inbound: InboundMessage | None = None,
        channel: TransportChannel | str | None = None,
        chat_id: int | None = None,
        raw_answer: str | None = None,
        source: str = "outbound",
    ) -> None:
        """Записать ответ в журнал, если пользователю ушло сообщение с «Ошибка»."""
        ch = channel
        cid = chat_id
        conv_id: str | None = None
        if inbound is not None:
            ch = inbound.channel
            cid = inbound.chat_id
            conv_id = inbound.conversation_id
        channel_str = ch.value if isinstance(ch, TransportChannel) else str(ch or "")
        journal_error_response_if_needed(
            answer=answer,
            user_query=user_query,
            channel=channel_str,
            chat_id=cid,
            conversation_id=conv_id,
            raw_answer=raw_answer,
            source=source,
        )
        journal_parse_failure_from_response(
            answer=answer,
            user_query=user_query,
            channel=channel_str,
            chat_id=cid,
            conversation_id=conv_id,
            raw_answer=raw_answer,
            source=source,
        )

    async def _format(
        self,
        answer: str,
        user_question: str,
        channel: TransportChannel = TransportChannel.TELEGRAM,
    ) -> str:
        """Форматировать ответ через FormatterAgent (с graceful fallback)."""
        if self._formatter and self._formatter.is_initialized:
            try:
                return await self._formatter.format_response(
                    answer,
                    user_question=user_question,
                    chat_id=self.chat_id,
                    channel=channel.value,
                )
            except Exception as e:
                log.warning("FormatterAgent: ошибка форматирования (%s), отправляю как есть", e)
        return answer

    def _finalize_email(
        self,
        answer: str,
        inbound: InboundMessage,
        raw_answer: str = "",
        agent_result: AgentProcessResult | None = None,
    ) -> OutboundMessage:
        """Финализация ответа для email: при превышении лимита — вложение."""
        settings = get_settings().email

        st = session_tokens.get(self.chat_id)
        token_footer = st.format_compact() if st.requests > 0 else ""

        subject = inbound.metadata.get("subject", "")
        body, attachments = prepare_email_response(
            answer,
            settings,
            subject=subject,
            token_footer=token_footer,
        )

        if agent_result:
            attachments.extend(agent_result.attachments)
            if agent_result.chart_html:
                from bot.messages import Attachment

                attachments.append(
                    Attachment(
                        filename="chart.html",
                        content_type="text/html",
                        data=agent_result.chart_html.encode("utf-8"),
                    )
                )

        return OutboundMessage(
            text=body,
            channel=TransportChannel.EMAIL,
            format="html",
            subject=subject,
            attachments=attachments,
            metadata={"token_footer": token_footer, "raw_answer": raw_answer},
        )

    @staticmethod
    def _has_more_pages(pagination_ctx: dict[str, Any]) -> bool:
        total = int(pagination_ctx.get("total", 0))
        skip = int(pagination_ctx.get("skip", 0))
        shown = int(pagination_ctx.get("shown", 0))
        return total > skip + shown

    async def _fetch_all_for_email(
        self,
        pagination_ctx: dict,
        user_text: str,
        *,
        fallback: str,
        chat_id: int,
    ) -> str:
        """Загрузить все страницы OData для email-ответа."""
        agent = self._agents.get("odata") or self._default_agent
        fetch_all = cast(
            Callable[..., Awaitable[str]] | None,
            getattr(agent, "execute_all_pages_with_ctx", None),
        )
        if fetch_all is None:
            return fallback

        settings = get_settings()
        try:
            return await fetch_all(
                pagination_ctx,
                user_text,
                chat_id=chat_id,
                max_records=settings.email.max_fetch_records,
            )
        except Exception as e:
            log.warning("Email fetch-all failed: %s", e)
            return f"{fallback}\n\n⚠️ Не удалось загрузить все записи: {e}"

    def _finalize(
        self,
        answer: str,
        pagination_ctx: dict | None,
        raw_answer: str = "",
        attachments: list | None = None,
    ) -> ChatResponse:
        """Финальные шаги пайплайна: подпись с токенами, обрезка, санитизация, клавиатура."""
        # Подпись с токенами сессии
        st = session_tokens.get(self.chat_id)
        if st.requests > 0:
            answer += f"\n\n<i>{st.format_compact()}</i>"

        # Обрезка под лимит Telegram (caption для photo — 1024, для текста — message_max_length)
        max_len = get_settings().telegram.message_max_length
        has_photo = any(getattr(a, "content_type", "").startswith("image/") for a in (attachments or []))
        effective_max = 1024 if has_photo else max_len
        if len(answer) > effective_max:
            answer = answer[:effective_max] + "... (сообщение сокращено)"

        # Санитизация HTML и построение клавиатуры пагинации
        safe_answer = sanitize_telegram_html(answer)
        reply_markup = self._build_pagination_keyboard(pagination_ctx)

        return ChatResponse(
            text=safe_answer,
            reply_markup=reply_markup,
            raw_answer=raw_answer,
            attachments=list(attachments or []),
        )

    # -- pagination helpers (перенесены из bot.py) ---------------------------

    @staticmethod
    def _extract_pagination_context(history: list[dict]) -> dict | None:
        """Извлечь контекст пагинации из последнего assistant-сообщения."""
        if not history:
            return None
        for msg in reversed(history):
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                try:
                    data = json.loads(content)
                    if isinstance(data, dict) and ("entity" in data or data.get("mode") == "analytics"):
                        return data
                except (json.JSONDecodeError, TypeError):
                    pass
                break  # проверяем только последнее
        return None

    @staticmethod
    def _build_pagination_keyboard(pagination_ctx: dict | None) -> InlineKeyboardMarkup | None:
        """Построить inline-клавиатуру для пагинации."""
        if not pagination_ctx:
            return None
        if pagination_ctx.get("mode") == "analytics":
            return None
        total = pagination_ctx.get("total", 0)
        skip = pagination_ctx.get("skip", 0)
        shown = pagination_ctx.get("shown", 0)
        if skip + shown < total:
            top = pagination_ctx.get("top", 20)
            next_skip = skip + top
            return InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("➡️ Следующие", callback_data=f"page:{next_skip}")],
                ]
            )
        return None

    # -- cleanup -------------------------------------------------------------

    def clear(self) -> None:
        """Полная очистка состояния чата (история, токены, пагинация)."""
        self._history_mgr.clear(self.chat_id)
        self.clear_pagination_state()
        session_tokens.clear(self.chat_id)

    # -- stats ---------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Статистика чата."""
        history = self.history
        return {
            "chat_id": self.chat_id,
            "messages": len(history),
            "has_pagination": self._pagination_ctx is not None,
        }


# ---------------------------------------------------------------------------
# ChatManager
# ---------------------------------------------------------------------------


class ChatManager:
    """Реестр чатов: фабрика + хранение.

    Создаёт экземпляры Chat по запросу, управляет их жизненным циклом.
    """

    def __init__(
        self,
        agents: dict[str, BaseAgent],
        formatter: FormatterAgent | None,
        history_mgr: HistoryManager,
    ) -> None:
        self._agents = agents
        self._formatter = formatter
        self._history_mgr = history_mgr
        self._chats: dict[int, Chat] = {}

    def get_or_create(self, chat_id: int | str) -> Chat:
        """Получить или создать чат по chat_id (int для Telegram, str conversation_id для email)."""
        if isinstance(chat_id, str):
            chat_id = conversation_id_to_chat_id(chat_id)
        if chat_id not in self._chats:
            agent = self._default_agent()
            if not agent:
                raise RuntimeError("Нет доступных агентов для обработки запроса")
            self._chats[chat_id] = Chat(
                chat_id=chat_id,
                agents=self._agents,
                default_agent=agent,
                formatter=self._formatter,
                history_mgr=self._history_mgr,
            )
        return self._chats[chat_id]

    def remove(self, chat_id: int) -> None:
        """Удалить чат из реестра."""
        self._chats.pop(chat_id, None)

    @property
    def chat_count(self) -> int:
        """Количество активных чатов."""
        return len(self._chats)

    def _default_agent(self) -> BaseAgent | None:
        """Вернуть агент по умолчанию (первый odata, или просто первый)."""
        if "odata" in self._agents:
            return self._agents["odata"]
        if self._agents:
            return next(iter(self._agents.values()))
        return None

    # -- delegated accessors -------------------------------------------------

    @property
    def agents(self) -> dict[str, BaseAgent]:
        """Доступ к реестру агентов."""
        return self._agents

    @property
    def formatter(self) -> FormatterAgent | None:
        """Доступ к форматтеру."""
        return self._formatter

    @property
    def history_mgr(self) -> HistoryManager:
        """Доступ к менеджеру истории."""
        return self._history_mgr
