#!/usr/bin/env python3
"""Агент-форматирование: преобразует текст ответа в красивый Telegram HTML.

Легковесный агент, который использует AI для переформатирования произвольного
текста в хорошо структурированный HTML-ответ для Telegram с эмодзи,
группировкой данных, правильными тегами и визуальным форматированием.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from openai import AsyncOpenAI

from bot.utils import RateLimiter
from bot.agents.base import BaseAgent
from .prompts import FORMATTER_SYSTEM

log = logging.getLogger(__name__)


class FormatterAgent(BaseAgent):
    """Агент для форматирования ответов в Telegram HTML."""

    name = "formatter"

    def __init__(self) -> None:
        super().__init__()
        self._ai_client: Optional[AsyncOpenAI] = None
        self._model: str = ""
        self._rate_limiter: Optional[RateLimiter] = None
        self._enabled: bool = True
        self._temperature: float = 0.2

    # -- lifecycle --

    async def initialize(
        self,
        agent_config: dict[str, Any],
        global_config: dict[str, Any],
        cache_dir: str = ".cache",
        env_file: str = "env.json",
    ) -> None:
        cfg = {**global_config, **agent_config}
        self._enabled = cfg.get("enabled", True)

        if not self._enabled:
            log.info("FormatterAgent отключён в конфигурации (enabled=false)")
            self._initialized = True
            return

        self._model = cfg.get("formatter_model") or cfg.get("ai_model", "gpt-4o-mini")

        self._ai_client = AsyncOpenAI(
            api_key=cfg["ai_api_key"],
            base_url=cfg.get("ai_base_url"),
            max_retries=0,
        )

        rpm = cfg.get("ai_rpm", 20)
        self._rate_limiter = RateLimiter(rpm=rpm)

        self._temperature = cfg.get("temperature", 0.2)

        self._initialized = True
        log.info("FormatterAgent инициализирован (model=%s, temperature=%s)", self._model, self._temperature)

    async def shutdown(self) -> None:
        self._initialized = False
        log.info("FormatterAgent остановлен")

    async def refresh(self) -> None:
        """Нет данных для обновления — noop."""
        pass

    # -- processing --

    async def process_message(
        self,
        user_text: str,
        history: list[dict[str, str]],
    ) -> tuple[str, list[dict[str, str]]]:
        """Не используется напрямую — форматирование через format_response()."""
        return user_text, history

    async def format_response(
        self,
        raw_answer: str,
        user_question: str = "",
    ) -> str:
        """Отформатировать ответ агента для Telegram.

        Args:
            raw_answer: исходный текст ответа (может быть plain text или частично HTML)
            user_question: оригинальный вопрос пользователя (для контекста)

        Returns:
            Отформатированный HTML-ответ для Telegram
        """
        if not self._enabled or not self._ai_client:
            return raw_answer

        # Если ответ уже хорошо отформатирован (содержит много тегов),
        # всё равно прогоняем через форматирование для стандартизации
        user_content = ""
        if user_question:
            user_content += f"Вопрос пользователя: {user_question}\n\n"
        user_content += f"Текст для форматирования:\n{raw_answer}"

        messages = [
            {"role": "system", "content": FORMATTER_SYSTEM},
            {"role": "user", "content": user_content},
        ]

        if self._rate_limiter:
            await self._rate_limiter.wait()

        try:
            resp = await self._ai_client.chat.completions.create(
                model=self._model,
                messages=messages,  # type: ignore[arg-type]
                temperature=self._temperature,
            )
            formatted = resp.choices[0].message.content or raw_answer
            log.info(
                "FormatterAgent: %d → %d символов",
                len(raw_answer), len(formatted),
            )
            return formatted
        except Exception:
            log.warning("FormatterAgent: ошибка форматирования, возврат исходного текста", exc_info=True)
            return raw_answer

    # -- status --

    def get_status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "initialized": self._initialized,
            "enabled": self._enabled,
            "model": self._model if self._enabled else "(disabled)",
        }