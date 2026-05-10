#!/usr/bin/env python3
"""Инкапсуляция вызовов AI (Шаг 1 + Шаг 2).

Отделяет AI-логику от бизнес-логики агента:
- :meth:`step1_build_query` — первый вызов AI для формирования OData-запроса
- :meth:`step2_format_response` — второй вызов AI для форматирования в HTML
- :meth:`handle_tool_call` — выполнение инструментов (search_entities, etc.)
- :meth:`resolve_tool_calls` — обработка function calls (до 2 раундов)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import AsyncOpenAI, BadRequestError

from bot.agents.odata.prompts import ODATA_REFERENCE, STEP2_SYSTEM
from bot.config import get_settings
from bot_lib.exceptions import AIError, AIResponseError

log = logging.getLogger(__name__)


class AIService:
    """Сервис взаимодействия с AI-провайдером."""

    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        rate_limiter: Any,
        metadata: Any,
        tools: list[dict],
        step1_temperature: float = 0.1,
        step2_temperature: float = 0.3,
        max_sample_records: int = 30,
        max_data_length: int = 8000,
    ) -> None:
        self._client = client
        self._model = model
        self._rate_limiter = rate_limiter
        self._metadata = metadata
        self._tools = tools
        self._step1_temperature = step1_temperature
        self._step2_temperature = step2_temperature
        self._max_sample_records = max_sample_records
        self._max_data_length = max_data_length

    # -- Step 1: Build OData query --

    async def step1_call_ai(
        self,
        messages: list[dict],
        use_tools: bool,
    ):
        """Вызов AI для Шага 1 — с инструментами или без."""
        from bot.metrics import metrics, save_provider_response, track_time

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,  # type: ignore[arg-type]
            "temperature": self._step1_temperature,
        }
        if use_tools and self._tools:
            kwargs["tools"] = self._tools
            kwargs["tool_choice"] = "auto"

        metrics.increment("ai_requests_step1")
        async with track_time("ai_step1"):
            try:
                resp = await self._client.chat.completions.create(**kwargs)  # type: ignore[union-attr]
            except BadRequestError:
                raise
            except Exception as exc:
                raise self._wrap_ai_error(exc) from exc

        self._track_ai_response(resp, "step1")

        save_provider_response(
            step="step1",
            model=self._model,
            request_messages=messages,
            response_data=resp.model_dump() if hasattr(resp, "model_dump") else str(resp),
        )
        return resp

    async def resolve_tool_calls(self, messages: list[dict], msg1) -> Any:
        """Обработать до 2 раундов function calls от AI.

        Args:
            messages: список сообщений (mutated — добавляются tool results).
            msg1: сообщение AI от Шага 1.

        Returns:
            Финальное сообщение AI после обработки всех tool calls.
        """
        for round_num in range(1, 3):
            if not msg1.tool_calls:
                break

            tool_results = []
            for tc in msg1.tool_calls:
                fn = tc.function
                log.info("Tool call (round %d): %s(%s)", round_num, fn.name, fn.arguments[:300] if fn.arguments else "")
                result = self.handle_tool_call(fn.name, json.loads(fn.arguments))
                log.info("Tool result (round %d): %s", round_num, result[:300] if result else "")
                tool_results.append({"role": "tool", "tool_call_id": tc.id, "content": result})

            messages.append(msg1.model_dump())  # type: ignore[arg-type]
            messages.extend(tool_results)

            if self._rate_limiter:
                await self._rate_limiter.wait()

            resp = await self.step1_call_ai(messages, use_tools=True)
            msg1 = resp.choices[0].message
            log.info("STEP1 after tools (round %d): content=%r",
                     round_num, (msg1.content or "")[:500] if msg1.content else None)

        return msg1

    # -- Step 2: Format response --

    async def step2_format_response(
        self,
        user_text: str,
        records: list[dict],
        total: int,
        entity: str,
        shown: int = 0,
        skip: int = 0,
        prev_last_record: dict | None = None,
    ) -> str:
        """Шаг 2: AI форматирует записи в HTML-ответ для Telegram."""
        from bot.agents.odata.response_parser import resolve_references
        from bot.metrics import metrics, save_provider_response, track_time

        resolved = resolve_references(records)
        sample = resolved[:self._max_sample_records]
        data_str = json.dumps(sample, ensure_ascii=False, indent=2)
        if len(data_str) > self._max_data_length:
            data_str = data_str[:self._max_data_length] + "\n... (данные сокращены)"

        pagination_info = ""
        if shown > 0 and total > shown:
            pagination_info = (
                f"\nПоказано записей: {shown} (пропущено: {skip})\n"
                f"Всего записей в выборке: {total}\nЕсть ещё записи для пагинации."
            )

        prev_item_info = ""
        if prev_last_record is not None:
            prev_resolved = resolve_references([prev_last_record])
            prev_json = json.dumps(prev_resolved[0], ensure_ascii=False, indent=2)
            prev_item_info = (
                f"\n\n⚠️ ПОСЛЕДНИЙ ЭЛЕМЕНТ С ПРЕДЫДУЩЕЙ СТРАНИЦЫ "
                f"(показать как контекст-напоминание, "
                f"без нумерации, курсивом, с разделителем '─── предыдущая страница ───'):\n{prev_json}"
            )

        messages = [
            {"role": "system", "content": STEP2_SYSTEM},
            {"role": "user", "content": (
                f"Вопрос: {user_text}\n\nСущность: {entity}\n"
                f"Всего записей: {total}{pagination_info}{prev_item_info}\n\n"
                f"Данные:\n{data_str}"
            )},
        ]

        if self._rate_limiter:
            await self._rate_limiter.wait()

        metrics.increment("ai_requests_step2")
        async with track_time("ai_step2"):
            try:
                resp = await self._client.chat.completions.create(  # type: ignore[union-attr]
                    model=self._model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=self._step2_temperature,
                )
            except Exception as exc:
                raise self._wrap_ai_error(exc) from exc

        self._track_ai_response(resp, "step2")

        save_provider_response(
            step="step2",
            model=self._model,
            request_messages=messages,
            response_data=resp.model_dump() if hasattr(resp, "model_dump") else str(resp),
        )

        content = resp.choices[0].message.content
        if not content:
            raise AIResponseError("AI вернул пустой ответ на шаге форматирования")
        return content

    # -- Tool execution --

    def handle_tool_call(self, name: str, args: dict) -> str:
        """Обработка вызова инструмента (function calling)."""
        if name == "odata_reference":
            topic = args.get("topic", "")
            return ODATA_REFERENCE.get(topic, f"Тема '{topic}' не найдена.")
        elif name == "get_entity_fields":
            entity_name = args.get("entity_name", "")
            fields = self._metadata.get_entity_fields(entity_name)
            if fields:
                return json.dumps({"entity": entity_name, "fields": fields}, ensure_ascii=False)
            return f"Сущность '{entity_name}' не найдена в метаданных."
        elif name == "search_entities":
            query = args.get("query", "")
            results = self._metadata.search_entities(query)
            if results:
                return json.dumps({"query": query, "results": results, "count": len(results)}, ensure_ascii=False)
            return f"По запросу '{query}' ничего не найдено. Попробуйте другое ключевое слово."
        return f"Неизвестный инструмент: {name}"

    # -- Helpers --

    @staticmethod
    def is_tool_use_error(exc: BadRequestError) -> bool:
        """Проверить, связана ли ошибка с отсутствием поддержки tool use."""
        msg = str(exc).lower()
        return "tool use" in msg or "tool_choice" in msg or "functions" in msg

    @staticmethod
    def _wrap_ai_error(exc: Exception) -> AIError:
        """Обернуть ошибку AI-провайдера в типизированное исключение."""
        from bot_lib.exceptions import AIRateLimitError
        msg = str(exc).lower()
        if "429" in msg or "rate" in msg or "limit" in msg:
            return AIRateLimitError(f"Превышен лимит запросов: {exc}")
        return AIError(f"Ошибка AI-сервиса: {exc}")

    def _track_ai_response(self, response, step: str) -> None:
        """Извлечь usage из ответа AI и записать в метрики."""
        from bot.metrics import metrics, session_tokens

        usage = getattr(response, "usage", None)
        if not usage:
            return

        settings = get_settings()
        pricing = settings.ai_pricing
        input_price, output_price = pricing.get_prices(self._model)

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

        # Chat ID передаётся извне через ODataState
        log.debug(
            "AI usage [%s]: model=%s in=%d out=%d cost_rub=%s",
            step, self._model, in_tok, out_tok, cost_rub,
        )

    def track_ai_response_with_chat(self, response, step: str, chat_id: int | None) -> None:
        """Полный трекинг AI-ответа включая per-session токены."""
        from bot.metrics import metrics, session_tokens

        usage = getattr(response, "usage", None)
        if not usage:
            return

        settings = get_settings()
        pricing = settings.ai_pricing
        input_price, output_price = pricing.get_prices(self._model)

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
            "AI usage [%s]: model=%s in=%d out=%d cost_rub=%s chat_id=%s",
            step, self._model, in_tok, out_tok, cost_rub, chat_id,
        )