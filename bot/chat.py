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
from dataclasses import dataclass, field
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.agents.base import BaseAgent
from bot.agents.formatter import FormatterAgent
from bot.config import get_settings
from bot.history import HistoryManager
from bot.metrics import session_tokens
from bot.utils import sanitize_telegram_html

log = logging.getLogger(__name__)


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
    """

    text: str
    reply_markup: InlineKeyboardMarkup | None = None
    raw_answer: str = ""


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
        agent: BaseAgent,
        formatter: FormatterAgent | None,
        history_mgr: HistoryManager,
    ) -> None:
        self.chat_id = chat_id
        self._agent = agent
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

    # -- core processing -----------------------------------------------------

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
        history = self.history

        # Шаг 1: обработка агентом
        answer, updated_history = await self._agent.process_message(
            user_text, history, chat_id=self.chat_id,
        )

        # Сохранить историю
        self.save_history(updated_history)

        raw_answer = answer

        # Шаг 2: форматирование через FormatterAgent
        if self._formatter and self._formatter.is_initialized:
            try:
                answer = await self._formatter.format_response(
                    answer, user_question=user_text, chat_id=self.chat_id,
                )
            except Exception as e:
                log.warning("FormatterAgent: ошибка форматирования (%s), отправляю как есть", e)

        # Шаг 3: подпись с токенами сессии
        st = session_tokens.get(self.chat_id)
        if st.requests > 0:
            answer += f"\n\n<i>{st.format_compact()}</i>"

        # Шаг 4: обрезка (Telegram limit)
        settings = get_settings()
        max_len = settings.telegram.message_max_length
        if len(answer) > max_len:
            answer = answer[:max_len] + "... (сообщение сокращено)"

        # Шаг 5: санитизация HTML
        safe_answer = sanitize_telegram_html(answer)

        # Шаг 6: пагинация
        reply_markup = None
        pagination_ctx = self._extract_pagination_context(updated_history)
        if pagination_ctx:
            self.save_pagination_state(pagination_ctx)
            reply_markup = self._build_pagination_keyboard(pagination_ctx)

        return ChatResponse(
            text=safe_answer,
            reply_markup=reply_markup,
            raw_answer=raw_answer,
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
                    if isinstance(data, dict) and "entity" in data:
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

    def get_or_create(self, chat_id: int) -> Chat:
        """Получить или создать чат по chat_id."""
        if chat_id not in self._chats:
            agent = self._default_agent()
            if not agent:
                raise RuntimeError("Нет доступных агентов для обработки запроса")
            self._chats[chat_id] = Chat(
                chat_id=chat_id,
                agent=agent,
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