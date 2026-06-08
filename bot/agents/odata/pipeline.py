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
from bot.agents.odata.analytics_executor import AnalyticsExecutor
from bot.agents.odata.attachment_csv import try_handle_csv_attachment
from bot.agents.odata.chart_renderer import render_html, render_png
from bot.agents.odata.conf_doc_context import fetch_conf_doc_context
from bot.agents.odata.config_hint_loader import format_config_hint_block
from bot.agents.odata.error_handler import QueryError
from bot.agents.odata.parse_failure import (
    build_parse_failure_message,
    classify_step1_failure,
    find_latest_step1_artifact,
    journal_parse_failure,
)
from bot.agents.odata.prompts import ODATA_REFERENCE, STEP1_SYSTEM
from bot.agents.odata.query_executor import QueryExecutor
from bot.agents.odata.query_validator import QueryValidator
from bot.agents.odata.request_brief_advisor import RequestBriefAdvisor, brief_from_rules, extract_current_query
from bot.agents.odata.request_guard import check_request_allowed, guard_limits_from_settings
from bot.agents.odata.response_headline import apply_request_headline
from bot.agents.odata.state import ODataState
from bot.agents.odata.tool_resolver import (
    AutoSearchResolver,
    DsmlToolCallResolver,
    InlineJsonResolver,
    NativeFunctionCallResolver,
    TextToolCallResolver,
    ToolResolver,
)
from bot.agents.odata.visualization_advisor import VisualizationAdvisor
from bot.config import ConfDocSettings
from bot.messages import Attachment
from bot.utils import esc_html
from bot_lib.dataframe import dataframe_preview_html

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
        max_analytics_records: int = 500,
        max_analytics_joins: int = 3,
        chart_max_categories: int = 30,
        config_hint_path: str | None = None,
        conf_doc: ConfDocSettings | None = None,
        analyst_service: Any | None = None,
    ) -> None:
        self._ai = ai
        self._executor = executor
        self._validator = validator
        self._metadata = metadata
        self._rate_limiter = rate_limiter
        self._tools = tools
        self._model = model
        self._history_max_turns = history_max_turns
        self._max_analytics_records = max_analytics_records
        self._max_analytics_joins = max_analytics_joins
        self._chart_max_categories = chart_max_categories
        self._config_hint_path = config_hint_path
        self._conf_doc = conf_doc or ConfDocSettings()
        self._conf_doc_block = ""
        self._analyst_service = analyst_service
        self._analyst_block = ""
        self._tools_supported: bool = True
        self._request_brief = RequestBriefAdvisor()

        # Цепочка резолверов tool calls
        self._tool_chain: ToolResolver = self._build_tool_chain()

    def _build_tool_chain(self) -> ToolResolver:
        """Построить цепочку резолверов (Chain of Responsibility)."""
        return NativeFunctionCallResolver(
            InlineJsonResolver(DsmlToolCallResolver(TextToolCallResolver(AutoSearchResolver(metadata=self._metadata))))
        )

    def set_analyst_service(self, service: Any) -> None:
        """Подключить AnalystService (pre-step)."""
        self._analyst_service = service

    def build_step1_prompt(self) -> str:
        """Построить системный промпт для Шага 1."""
        base = STEP1_SYSTEM.format(metadata=self._metadata.format_entity_list())
        base += format_config_hint_block(self._config_hint_path)
        if self._analyst_block:
            base += "\n\n--- АНАЛИЗ МЕТАДАННЫХ (analyst) ---\n" + self._analyst_block + "\n--- КОНЕЦ АНАЛИЗА ---\n"
        if self._conf_doc_block:
            base += "\n\n--- КОНТЕКСТ ИЗ ДОКУМЕНТАЦИИ КОНФИГУРАЦИИ (conf-doc) ---\n" + self._conf_doc_block
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

        guard = check_request_allowed(user_text)
        if guard.blocked:
            log.info("Request blocked by guard: %s", guard.reason)
            state.request_brief = brief_from_rules(extract_current_query(user_text))
            state.answer_html = guard.message_html(limits=guard_limits_from_settings())
            state = self._finalize_answer(state)
            state.history = state.finalize_history(
                self._history_max_turns,
                assistant_content=state.answer_html,
            )
            return state

        csv_state = await try_handle_csv_attachment(state, self._ai)
        if csv_state is not None:
            return self._finalize_answer(csv_state)

        state.request_brief = await self._request_brief.advise(
            self._ai,
            user_query=state.user_text,
            history=state.history,
            chat_id=state.chat_id,
        )
        log.info(
            "RequestBrief: %s (source=%s)",
            state.request_brief.headline[:80],
            state.request_brief.source,
        )

        brief_headline = state.request_brief.headline if state.request_brief else None
        await self._step_analyze(state, request_brief=brief_headline)
        self._conf_doc_block = await fetch_conf_doc_context(
            state.user_text,
            self._conf_doc,
            request_brief=brief_headline,
        )

        # Шаг 1: AI формирует OData-запрос
        await self._step_build_query(state)

        # Analytics-ветка (multi-query, join, графики)
        if state.analytics_plan:
            return self._finalize_answer(await self._step_analytics(state))

        # Проверка entity
        if not state.query or not state.query.entity:
            raise QueryError("Не указана сущность (entity) в запросе.")

        # Проверка count-запроса
        if state.query.count:
            return self._finalize_answer(await self._step_count_query(state))

        # Шаг 2: Валидация
        await self._step_validate(state)

        # Шаг 3: Выполнение OData
        await self._step_execute(state)

        # Шаг 4: Форматирование через AI
        await self._step_format(state)

        # Финализация истории
        state.history = state.finalize_history(self._history_max_turns)

        return self._finalize_answer(state)

    def _finalize_answer(self, state: ODataState) -> ODataState:
        """Добавить заголовок, соотнесённый с запросом пользователя."""
        state.answer_html = apply_request_headline(state.answer_html, state.request_brief)
        return state

    # -- Pipeline steps (potential LangGraph nodes) --

    async def _step_analyze(self, state: ODataState, request_brief: str | None = None) -> None:
        """Pre-step: AnalystService определяет релевантные объекты метаданных."""
        self._analyst_block = ""
        if not self._analyst_service:
            return
        try:
            brief = await self._analyst_service.analyze(
                state.user_text,
                chat_id=state.chat_id,
                request_brief=request_brief,
            )
            if brief.primary_objects or brief.intent or brief.notes:
                self._analyst_block = brief.to_prompt_block()
                log.info(
                    "Analyst pre-step: intent=%s, primary=%d",
                    brief.intent,
                    len(brief.primary_objects),
                )
        except Exception as exc:
            log.warning("Analyst pre-step failed: %s", exc)

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
            resp = await self._ai.step1_call_ai(state.ai_messages, use_tools=use_tools, chat_id=state.chat_id)
        except BadRequestError as e:
            if use_tools and AIService.is_tool_use_error(e):
                log.warning("Модель %s не поддерживает tool use, повтор без инструментов", self._model)
                self._tools_supported = False
                state.tools_supported = False
                system_prompt = self.build_step1_prompt()
                state.ai_messages[0] = {"role": "system", "content": system_prompt}
                if self._rate_limiter:
                    await self._rate_limiter.wait()
                resp = await self._ai.step1_call_ai(state.ai_messages, use_tools=False, chat_id=state.chat_id)
            else:
                raise

        msg = resp.choices[0].message
        log.info(
            "STEP1 initial: content=%r, tool_calls=%s",
            (msg.content or "")[:200] if msg.content else None,
            [tc.function.name for tc in msg.tool_calls] if msg.tool_calls else None,
        )

        # Обработка function calls (до 2 раундов)
        if msg.tool_calls:
            msg = await self._ai.resolve_tool_calls(state.ai_messages, msg, chat_id=state.chat_id)

        state.ai_response_content = msg.content or ""
        log.info("STEP1 final content (len=%d): %s", len(state.ai_response_content), state.ai_response_content[:1000])

        from bot.agents.odata.query_parser import get_last_apply_failure
        from bot.agents.odata.tool_resolver import _extract_json

        if self._try_apply_step1_content(state, state.ai_response_content):
            self._log_step1_success(state)
            return

        # Цепочка резолверов (inline tool call, text tool call, auto-search)
        state.query = await self._tool_chain.resolve(state, self._ai)

        if state.analytics_plan:
            log.info(
                "STEP1 resolved analytics via tool chain: queries=%d chart=%s",
                len(state.analytics_plan.queries),
                state.analytics_plan.chart.type if state.analytics_plan.chart else None,
            )
            return

        if state.query:
            log.info("STEP1 resolved query: entity=%s filter=%s", state.query.entity, state.query.filter_expr)
            return

        # Retry при пустом ответе AI
        if not (state.ai_response_content or "").strip():
            await self._retry_step1_json(state, "Предыдущий ответ был пустым. Верни ТОЛЬКО JSON.")
            if state.analytics_plan or state.query:
                return

        # Финальный retry: явная просьба вернуть только JSON
        await self._retry_step1_json(
            state,
            f"Предыдущий ответ не был валидным JSON для OData/analytics.\nПострой JSON для запроса: {state.user_text}",
        )
        if state.analytics_plan or state.query:
            return

        query_dict = _extract_json(state.ai_response_content or "")
        reason = classify_step1_failure(
            state.ai_response_content,
            query_dict=query_dict,
            apply_failed_reason=get_last_apply_failure(),
        )
        log.warning("Не удалось извлечь JSON из ответа AI. Полный ответ:\n%s", state.ai_response_content)
        journal_parse_failure(
            user_query=state.user_text,
            ai_response=state.ai_response_content,
            reason=reason,
            chat_id=state.chat_id,
            step1_artifact=find_latest_step1_artifact(),
        )
        raise QueryError(build_parse_failure_message(state.ai_response_content))

    async def _retry_step1_json(self, state: ODataState, intro: str) -> None:
        """Повтор Step1 с просьбой вернуть только JSON."""
        from bot.agents.odata.tool_resolver import RETRY_JSON_HINT

        retry_msg = f"{intro}\n{RETRY_JSON_HINT}"
        state.ai_messages.append({"role": "assistant", "content": state.ai_response_content or ""})
        state.ai_messages.append({"role": "user", "content": retry_msg})
        if self._rate_limiter:
            await self._rate_limiter.wait()
        try:
            resp = await self._ai.step1_call_ai(state.ai_messages, use_tools=False, chat_id=state.chat_id)
            state.ai_response_content = resp.choices[0].message.content or ""
            log.info(
                "STEP1 retry content (len=%d): %s",
                len(state.ai_response_content),
                state.ai_response_content[:1000],
            )
            if self._try_apply_step1_content(state, state.ai_response_content):
                self._log_step1_success(state)
                return
            state.query = await self._tool_chain.resolve(state, self._ai)
        except Exception as exc:
            log.warning("STEP1 retry failed: %s", exc)

    def _try_apply_step1_content(self, state: ODataState, content: str) -> bool:
        from bot.agents.odata.query_parser import apply_step1_dict
        from bot.agents.odata.tool_resolver import _extract_json

        query_dict = _extract_json(content)
        return bool(query_dict and apply_step1_dict(state, query_dict))

    def _log_step1_success(self, state: ODataState) -> None:
        if state.analytics_plan:
            log.info(
                "STEP1 parsed analytics: queries=%d joins=%d chart=%s",
                len(state.analytics_plan.queries),
                len(state.analytics_plan.joins),
                state.analytics_plan.chart.type if state.analytics_plan.chart else None,
            )
        elif state.query:
            log.info(
                "STEP1 parsed query: entity=%s filter=%s",
                state.query.entity,
                state.query.filter_expr,
            )

    async def _step_validate(self, state: ODataState) -> None:
        """Шаг 2: Валидация OData-запроса по метаданным."""
        assert state.query is not None
        validated = self._validator.validate(state.query)
        # Обновляем query валидированными параметрами
        state.query.select = validated["select"]
        state.query.orderby = validated["orderby"]
        state.query.top = validated["top"]
        state.query.skip = validated["skip"]
        state.query.expand = validated["expand"]

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
            expand=q.expand,
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
            chat_id=state.chat_id,
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

        answer = f"<i>{esc_html(q.entity)}</i>: <code>{total}</code>"
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

    async def _step_analytics(self, state: ODataState) -> ODataState:
        """Analytics: multi-query → pandas → таблица и/или график."""
        assert state.analytics_plan is not None
        plan = state.analytics_plan

        analytics_exec = AnalyticsExecutor(
            self._executor,
            metadata=self._metadata,
            max_records=self._max_analytics_records,
            max_joins=self._max_analytics_joins,
        )

        try:
            result = await analytics_exec.execute(plan, user_query=state.user_text)
        except (ValueError, KeyError) as e:
            raise QueryError(f"Ошибка аналитики: {esc_html(str(e))}") from e

        plan = result.plan
        df = result.dataframe

        advisor = VisualizationAdvisor(
            pie_max_categories=10,
            max_categories=self._chart_max_categories,
        )
        viz = await advisor.advise(
            self._ai,
            user_query=state.user_text,
            df=df,
            plan=plan,
            chat_id=state.chat_id,
        )
        log.info(
            "VisualizationAdvisor: chart=%s table=%s source=%s reason=%s",
            viz.show_chart,
            viz.show_table,
            viz.source,
            viz.reason[:120] if viz.reason else "",
        )

        if viz.chart:
            plan = plan.model_copy(update={"chart": viz.chart})
        elif not viz.show_chart:
            plan = plan.model_copy(update={"chart": None})
        state.analytics_plan = plan

        parts: list[str] = []
        if plan.explanation:
            parts.append(f"<i>{esc_html(plan.explanation)}</i>")
        if viz.reason and viz.source == "ai":
            parts.append(f"<small>Формат: {esc_html(viz.reason)}</small>")
        if viz.show_table:
            parts.append(dataframe_preview_html(df, max_rows=20))
        else:
            parts.append(f"<i>Строк в выборке: {len(df)}</i>")
        state.answer_html = "\n".join(parts)
        state.dataframe_summary = f"Строк: {len(df)}"

        if viz.show_chart and plan.chart:
            try:
                png_bytes = render_png(
                    df,
                    plan.chart,
                    max_categories=self._chart_max_categories,
                )
                state.attachments.append(
                    Attachment(
                        filename="chart.png",
                        content_type="image/png",
                        data=png_bytes,
                    )
                )
                state.chart_html = render_html(
                    df,
                    plan.chart,
                    max_categories=self._chart_max_categories,
                )
            except Exception as e:
                log.warning("Chart render failed: %s", e)
                state.answer_html += f"\n\n⚠️ Не удалось построить график: {esc_html(str(e))}"

        ctx = plan.to_history_ctx() | {"total": len(df), "shown": len(df)}
        state.pagination_ctx = ctx
        state.history = state.finalize_history(
            self._history_max_turns,
            assistant_content=json.dumps(ctx, ensure_ascii=False),
        )
        return state

    @property
    def tools_supported(self) -> bool:
        return self._tools_supported

    @tools_supported.setter
    def tools_supported(self, value: bool) -> None:
        self._tools_supported = value
