#!/usr/bin/env python3
"""Pipeline обработки OData-запросов.

Последовательность этапов::

    build_query → resolve_tools → validate → execute → format

Каждый этап получает и мутирует :class:`ODataState`.
Pipeline реализован как явная цепочка вызовов — без LangGraph, но
с возможностью лёгкого перехода на него (каждый шаг = node).

Использование::

    pipeline = ODataPipeline(ai=ai_service, executor=executor, ...)
    state = await pipeline.run(user_text, history, chat_id=chat_id)
    return state.answer_html, state.history
"""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import BadRequestError

from bot.agents.odata.ai_service import AIService
from bot.agents.odata.error_handler import QueryError
from bot.agents.odata.query_executor import QueryExecutor
from bot.agents.odata.query_validator import QueryValidator
from bot.agents.odata.state import ODataQuery, ODataState
from bot.agents.odata.tool_resolver import (
    AutoSearchResolver,
    InlineJsonResolver,
    NativeFunctionCallResolver,
    TextToolCallResolver,
    ToolResolver,
)
from bot.agents.odata.prompts import ODATA_REFERENCE, STEP1_SYSTEM
from bot.utils import esc_html

log = logging.getLogger(__name__)


class ODataPipeline:
    """Оркестратор pipeline обработки OData-запросов.

    Каждый метод ``_step_*`` — потенциальный node в LangGraph.
    """

    def __init__(
        self,
        ai: AIService,
        executor: QueryExecutor,
        validator: QueryValidator,
        metadata: Any,
        rate_limiter: Any,
        tools: list[dict],
        model: str,
        history_max_turns: int = 10,
        default_top: int = 20,
    ) -> None:
        self._ai = ai
        self._executor = executor
        self._validator = validator
        self._metadata = metadata
        self._rate_limiter = rate_limiter
        self._tools = tools
        self._model = model
        self._history_max_turns = history_max_turns
        self._default_top = default_top
        self._tools_supported: bool = True

        # Цепочка резолверов tool calls
        self._tool_chain: ToolResolver = self._build_tool_chain()

    def _build_tool_chain(self) -> ToolResolver:
        """Построить цепочку резолверов (Chain of Responsibility)."""
        return NativeFunctionCallResolver(
            InlineJsonResolver(
                TextToolCallResolver(
                    AutoSearchResolver(metadata=self._metadata)
                )
            )
        )

    def build_step1_prompt(self) -> str:
        """Построить системный промпт для Шага 1."""
        base = STEP1_SYSTEM.format(metadata=self._metadata.format_entity_list())
        if not self._tools_supported:
            ref_lines = [
                "\n\n--- СПРАВОЧНИК ODATA (инструменты недоступны через function calling, используй текстовый вызов) ---",
                "⚠️ Инструменты НЕ доступны через function calling. Вызывай их текстом:",
                "  search_entities(query='Организации')",
                "  get_entity_fields(entity_name='Catalog_Организации')",
                "  odata_reference(topic='filter')",
                "Система распознает текстовый вызов, выполнит его и автоматически повторит запрос с результатом.",
                "После получения результата — ТОЛЬКО JSON с entity/filter/select, без рассуждений!",
                "",
                "Если ты УВЕРЕН(А) в имени сущности (видишь точное совпадение в списке доступных объектов),",
                "используй его напрямую без вызова инструмента.",
            ]
            for topic, text in ODATA_REFERENCE.items():
                ref_lines.append(f"\n[{topic}]\n{text}")
            base += "\n".join(ref_lines)
        return base

    # -- Main entry point --

    async def run(
        self,
        user_text: str,
        history: list[dict[str, str]],
        chat_id: int | None = None,
    ) -> ODataState:
        """Выполнить полный pipeline обработки.

        Returns:
            :class:`ODataState` с заполненным ``answer_html`` и ``history``.
        """
        state = ODataState(
            user_text=user_text,
            chat_id=chat_id,
            history=list(history),
            tools_supported=self._tools_supported,
        )

        # Шаг 1: AI формирует OData-запрос
        await self._step_build_query(state)

        # Проверка entity
        if not state.query or not state.query.entity:
            raise QueryError("Не указана сущность (entity) в запросе.")

        # Проверка count-запроса
        if state.query.count:
            return await self._step_count_query(state)

        # Шаг 2: Валидация
        await self._step_validate(state)

        # Шаг 3: Выполнение OData
        await self._step_execute(state)

        # Шаг 4: Форматирование через AI
        await self._step_format(state)

        # Финализация истории
        state.history = state.finalize_history(self._history_max_turns)

        return state

    # -- Pipeline steps (potential LangGraph nodes) --

    async def _step_build_query(self, state: ODataState) -> None:
        """Шаг 1: AI формирует OData-запрос с разрешением tool calls."""
        system_prompt = self.build_step1_prompt()
        state.ai_messages = [
            {"role": "system", "content": system_prompt},
            *state.history,
            {"role": "user", "content": state.user_text},
        ]

        if self._rate_limiter:
            await self._rate_limiter.wait()

        use_tools = state.tools_supported
        try:
            resp = await self._ai.step1_call_ai(state.ai_messages, use_tools=use_tools)
        except BadRequestError as e:
            if use_tools and AIService.is_tool_use_error(e):
                log.warning("Модель %s не поддерживает tool use, повтор без инструментов", self._model)
                self._tools_supported = False
                state.tools_supported = False
                system_prompt = self.build_step1_prompt()
                state.ai_messages[0] = {"role": "system", "content": system_prompt}
                if self._rate_limiter:
                    await self._rate_limiter.wait()
                resp = await self._ai.step1_call_ai(state.ai_messages, use_tools=False)
            else:
                raise

        msg = resp.choices[0].message
        log.info("STEP1 initial: content=%r, tool_calls=%s",
                 (msg.content or "")[:200] if msg.content else None,
                 [tc.function.name for tc in msg.tool_calls] if msg.tool_calls else None)

        # Трекинг AI-ответа с chat_id
        self._ai.track_ai_response_with_chat(resp, "step1", state.chat_id)

        # Обработка function calls (до 2 раундов)
        if msg.tool_calls:
            msg = await self._ai.resolve_tool_calls(state.ai_messages, msg)
            # Трекинг ответа после tool resolution
        state.ai_response_content = msg.content or ""
        log.info("STEP1 final content (len=%d): %s", len(state.ai_response_content), state.ai_response_content[:1000])

        # Попытка извлечь JSON напрямую
        from bot.agents.odata.tool_resolver import _extract_json
        query_dict = _extract_json(state.ai_response_content)

        # Проверка inline tool call
        if query_dict and self._is_inline_tool_call(query_dict):
            log.info("Detected inline tool call in JSON, resolving...")
            # InlineJsonResolver обработает это
            state.query = None
        elif query_dict and query_dict.get("entity"):
            state.query = ODataQuery.from_dict(query_dict)
            log.info("STEP1 parsed query: %s", json.dumps(query_dict, ensure_ascii=False)[:500])
            return

        # Цепочка резолверов (fallback)
        state.query = await self._tool_chain.resolve(state, self._ai)

        if not state.query:
            log.warning("Не удалось извлечь JSON из ответа AI. Полный ответ:\n%s", state.ai_response_content)
            raise QueryError(
                f"Не удалось разобрать запрос. Попробуйте переформулировать.\n\n"
                f"<pre>{esc_html(state.ai_response_content[:500])}</pre>"
            )

        log.info("STEP1 resolved query: entity=%s filter=%s",
                 state.query.entity, state.query.filter_expr)

    async def _step_validate(self, state: ODataState) -> None:
        """Шаг 2: Валидация OData-запроса по метаданным."""
        assert state.query is not None
        validated = self._validator.validate(state.query)
        # Обновляем query валидированными параметрами
        state.query.select = validated["select"]
        state.query.orderby = validated["orderby"]
        state.query.top = validated["top"]
        state.query.skip = validated["skip"]
        # expand храним отдельно в pagination_ctx (не в query)

    async def _step_execute(self, state: ODataState) -> None:
        """Шаг 3: Выполнение OData-запроса с fallback-стратегиями."""
        assert state.query is not None
        q = state.query
        records, total = await self._executor.execute(
            entity=q.entity,
            filter_expr=q.filter_expr,
            select=q.select,
            orderby=q.orderby,
            top=q.top,
            skip=q.skip or None,
            expand=state.pagination_ctx.get("expand") if state.pagination_ctx else None,
        )
        state.records = records
        state.total = total

    async def _step_format(self, state: ODataState) -> None:
        """Шаг 4: AI форматирует результат в HTML для Telegram."""
        assert state.query is not None
        q = state.query
        shown = len(state.records)

        answer = await self._ai.step2_format_response(
            user_text=state.user_text,
            records=state.records,
            total=state.total,
            entity=q.entity,
            shown=shown,
            skip=q.skip or 0,
        )
        state.answer_html = answer

        # Сохранить контекст пагинации
        state.pagination_ctx = q.to_pagination_ctx() | {
            "total": state.total,
            "shown": shown,
        }

    async def _step_count_query(self, state: ODataState) -> ODataState:
        """Обработать count-запрос (без форматирования AI)."""
        assert state.query is not None
        q = state.query

        records, total = await self._executor.execute_count(
            entity=q.entity,
            filter_expr=q.filter_expr,
        )

        answer = f"<b>📊 Количество</b> <i>{esc_html(q.entity)}</i>: <code>{total}</code>"
        if q.explanation:
            answer += f"\n<i>{esc_html(q.explanation)}</i>"

        state.answer_html = answer
        state.total = total
        state.pagination_ctx = q.to_pagination_ctx() | {"count": True}

        state.history = state.finalize_history(
            self._history_max_turns,
            assistant_content=json.dumps(
                {"entity": q.entity, "filter": q.filter_expr, "count": True, "explanation": q.explanation or ""},
                ensure_ascii=False,
            ),
        )
        return state

    # -- helpers --

    @staticmethod
    def _is_inline_tool_call(parsed: dict) -> bool:
        """Проверить, выглядит ли JSON как встроенный вызов инструмента."""
        tool_names = frozenset({"odata_reference", "get_entity_fields", "search_entities"})
        if not isinstance(parsed, dict) or "entity" in parsed:
            return False
        tool_name = parsed.get("name") or parsed.get("function")
        return tool_name in tool_names and isinstance(parsed.get("arguments"), dict)

    @property
    def tools_supported(self) -> bool:
        return self._tools_supported

    @tools_supported.setter
    def tools_supported(self, value: bool) -> None:
        self._tools_supported = value