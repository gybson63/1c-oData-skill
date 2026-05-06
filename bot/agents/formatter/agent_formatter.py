#!/usr/bin/env python3
"""Агент-форматирование: преобразует текст ответа в красивый Telegram HTML.

Легковесный агент, который использует AI для переформатирования произвольного
текста в хорошо структурированный HTML-ответ для Telegram с эмодзи,
группировкой данных, правильными тегами и визуальным форматированием.
"""

from __future__ import annotations

import logging
from typing import Any

from openai import AsyncOpenAI

from bot.agents.base import BaseAgent
from bot.config import get_settings
from bot.metrics import metrics, save_provider_response, session_tokens, track_time
from bot.utils import RateLimiter
from bot_lib.exceptions import AIRateLimitError, AIResponseError

from .prompts import FORMATTER_SYSTEM

log = logging.getLogger(__name__)


class FormatterAgent(BaseAgent):
    """Агент для форматирования ответов в Telegram HTML."""

    name = "formatter"

    def __init__(self) -> None:
        super().__init__()
        self._ai_client: AsyncOpenAI | None = None
        self._model: str = ""
        self._rate_limiter: RateLimiter | None = None
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
        # Типизированные настройки через Pydantic Settings
        settings = get_settings()
        fmt = settings.formatter
        ai = settings.ai

        self._enabled = fmt.enabled

        if not self._enabled:
            log.info("FormatterAgent отключён в конфигурации (enabled=false)")
            self._initialized = True
            return

        self._model = fmt.formatter_model

        self._ai_client = AsyncOpenAI(
            api_key=ai.api_key,
            base_url=ai.base_url,
            max_retries=0,
        )

        self._rate_limiter = RateLimiter(rpm=ai.rpm)

        self._temperature = fmt.temperature

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
        *,
        chat_id: int | None = None,
    ) -> tuple[str, list[dict[str, str]]]:
        """Не используется напрямую — форматирование через format_response()."""
        return user_text, history

    async def format_response(
        self,
        raw_answer: str,
        user_question: str = "",
        *,
        chat_id: int | None = None,
    ) -> str:
        """Отформатировать ответ агента для Telegram.

        Args:
            raw_answer: исходный текст ответа (может быть plain text или частично HTML)
            user_question: оригинальный вопрос пользователя (для контекста)
            chat_id: ID чата для трекинга токенов по сессии

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

        metrics.increment("ai_requests_formatter")
        async with track_time("ai_formatter"):
            try:
                resp = await self._ai_client.chat.completions.create(
                    model=self._model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=self._temperature,
                )
            except Exception as exc:
                raise AIRateLimitError(f"AI API error in formatter: {exc}") from exc

        # Track AI usage (tokens + cost)
        usage = getattr(resp, "usage", None)
        if usage:
            settings = get_settings()
            pricing = settings.ai_pricing
            input_price, output_price = pricing.get_prices(self._model)
            # Извлечь cost_rub из ответа провайдера (если доступен)
            cost_rub = getattr(usage, "cost_rub", None)
            in_tok = usage.prompt_tokens or 0
            out_tok = usage.completion_tokens or 0
            metrics.track_ai_usage(
                model=self._model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                input_price_per_1m=input_price,
                output_price_per_1m=output_price,
                cost_rub=cost_rub,
            )
            # Записать токены и стоимость в per-session трекер
            if chat_id is not None:
                cost_usd = (in_tok * input_price + out_tok * output_price) / 1_000_000
                session_tokens.record(
                    chat_id,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    cost_usd=cost_usd,
                    cost_rub=cost_rub or 0.0,
                )
            log.debug(
                "AI usage [formatter]: model=%s in=%d out=%d cost_rub=%s chat_id=%s",
                self._model, in_tok, out_tok, cost_rub, chat_id,
            )

        # Сохранить ответ провайдера
        save_provider_response(
            step="formatter",
            model=self._model,
            request_messages=messages,
            response_data=resp.model_dump() if hasattr(resp, "model_dump") else str(resp),
        )

        try:
            formatted = resp.choices[0].message.content or raw_answer
        except (IndexError, AttributeError) as exc:
            raise AIResponseError(f"Некорректный ответ AI в форматере: {exc}") from exc

        log.info(
            "FormatterAgent: %d → %d символов",
            len(raw_answer), len(formatted),
        )
        return formatted

    # -- status --

    def get_status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "initialized": self._initialized,
            "enabled": self._enabled,
            "model": self._model if self._enabled else "(disabled)",
        }
