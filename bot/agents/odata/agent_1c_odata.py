#!/usr/bin/env python3
"""OData-агент: тонкий координатор pipeline обработки запросов к 1С.

Делегирует бизнес-логику модулям:
  - :mod:`bot.agents.odata.pipeline` — оркестрация build → validate → execute → format
  - :mod:`bot.agents.odata.ai_service` — вызовы AI (Шаг 1 + Шаг 2)
  - :mod:`bot.agents.odata.query_executor` — OData HTTP + fallback-стратегии
  - :mod:`bot.agents.odata.query_validator` — валидация по $metadata
  - :mod:`bot.agents.odata.error_handler` — централизованная обработка ошибок
  - :mod:`bot.agents.odata.metadata` — кэш и загрузка $metadata
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bot.mcp_client import MCPClientManager

from openai import AsyncOpenAI

from bot.agents.base import BaseAgent
from bot.agents.odata.ai_service import AIService
from bot.agents.odata.error_handler import ErrorHandler
from bot.agents.odata.metadata import MetadataCache, fetch_metadata_from_server
from bot.agents.odata.pipeline import ODataPipeline
from bot.agents.odata.prompts import ODATA_REFERENCE, make_step1_tools
from bot.agents.odata.query_executor import QueryExecutor
from bot.agents.odata.query_validator import QueryValidator
from bot.config import get_settings, parse_conf_doc_settings
from bot.mcp_config import resolve_mcp_config
from bot.messages import AgentProcessResult
from bot.utils import RateLimiter, esc_html
from bot_lib.exceptions import ODataError

log = logging.getLogger(__name__)


class ODataAgent(BaseAgent):
    """Агент для работы с 1С OData через pipeline."""

    name = "odata"

    def __init__(self) -> None:
        super().__init__()
        self._ai_client: AsyncOpenAI | None = None
        self._metadata = MetadataCache()
        self._mcp_manager: MCPClientManager | None = None
        self._cfg: dict[str, Any] = {}
        self._model: str = ""
        self._pipeline: ODataPipeline | None = None
        self._ai_service: AIService | None = None
        self._executor: QueryExecutor | None = None
        self._error_handler: ErrorHandler | None = None
        self._history_max_turns: int = 10
        self._default_top: int = 20
        self._request_timeout: int = 60
        self._analyst_service: Any = None

    def set_analyst_service(self, service: Any) -> None:
        """Подключить AnalystService для pre-step анализа метаданных."""
        self._analyst_service = service
        if self._pipeline:
            self._pipeline.set_analyst_service(service)

    def _auth_header(self) -> str:
        import base64

        u, p = self._cfg["odata_user"], self._cfg["odata_password"]
        token = base64.b64encode(f"{u}:{p}".encode()).decode()
        return f"Basic {token}"

    async def initialize(
        self,
        agent_config: dict[str, Any],
        global_config: dict[str, Any],
        cache_dir: str = ".cache",
        env_file: str = "env.json",
    ) -> None:
        self._cfg = {**global_config, **agent_config}

        settings = get_settings()
        ai = settings.ai
        odata = settings.odata_query

        self._model = ai.model
        self._metadata = MetadataCache(cache_dir, cache_seconds=odata.metadata_cache_seconds)
        self._history_max_turns = settings.history_max_turns
        self._default_top = odata.default_top
        self._request_timeout = odata.request_timeout

        self._ai_client = AsyncOpenAI(
            api_key=ai.api_key,
            base_url=ai.base_url,
            max_retries=0,
        )

        rate_limiter = RateLimiter(rpm=ai.rpm)
        ref_keys = list(ODATA_REFERENCE.keys())
        tools = make_step1_tools(ref_keys)

        self._ai_service = AIService(
            client=self._ai_client,
            model=self._model,
            rate_limiter=rate_limiter,
            metadata=self._metadata,
            tools=tools,
            step1_temperature=ai.temperature,
            step2_temperature=ai.temperature_step2,
            max_sample_records=odata.max_sample_records,
            max_data_length=odata.max_data_length,
            timeout_retry_count=ai.timeout_retry_count,
            timeout_retry_delay=ai.timeout_retry_delay,
        )

        auth = self._auth_header()
        self._executor = QueryExecutor(
            odata_url=self._cfg["odata_url"],
            auth_header=auth,
            request_timeout=self._request_timeout,
            metadata=self._metadata,
        )

        validator = QueryValidator(
            metadata=self._metadata,
            odata_url=self._cfg["odata_url"],
            default_top=odata.default_top,
            max_top=odata.max_top,
            max_expand_fields=odata.max_expand_fields,
            max_url_length=odata.max_url_length,
        )

        self._pipeline = ODataPipeline(
            ai=self._ai_service,
            executor=self._executor,
            validator=validator,
            metadata=self._metadata,
            rate_limiter=rate_limiter,
            tools=tools,
            model=self._model,
            history_max_turns=self._history_max_turns,
            max_analytics_records=odata.max_analytics_records,
            max_analytics_joins=odata.max_analytics_joins,
            chart_max_categories=odata.chart_max_categories,
            config_hint_path=agent_config.get("config_hint_path"),
            conf_doc=parse_conf_doc_settings(agent_config.get("conf_doc")),
            analyst_service=self._analyst_service,
        )

        self._error_handler = ErrorHandler(max_history_turns=self._history_max_turns)

        profile_cfg = global_config.get("profile_config") or {}
        mcp_config = resolve_mcp_config(profile_cfg, agent_config)
        if mcp_config:
            from bot.mcp_client import MCPClientManager

            self._mcp_manager = MCPClientManager()
            await self._mcp_manager.connect_all(mcp_config)
            if self._mcp_manager.is_connected():
                status = self._mcp_manager.get_status()
                for srv, info in status.items():
                    log.info(
                        "ODataAgent MCP [%s]: transport=%s, tools=%s",
                        srv,
                        info["transport"],
                        info["tools"],
                    )

        loaded = self._metadata.load_from_disk()
        if not loaded:
            await self._load_metadata(force=True)

        self._initialized = True
        log.info("ODataAgent инициализирован (сущностей: %d)", len(self._metadata.entities))

    async def shutdown(self) -> None:
        if self._mcp_manager:
            await self._mcp_manager.disconnect_all()
            self._mcp_manager = None
        self._pipeline = None
        self._ai_service = None
        self._executor = None
        self._initialized = False
        log.info("ODataAgent остановлен")

    async def refresh(self) -> None:
        await self._load_metadata(force=True)
        log.info("ODataAgent: метаданные обновлены (%d сущностей)", len(self._metadata.entities))

    async def _load_metadata(self, force: bool = False) -> None:
        if not force and self._metadata.is_loaded:
            return
        xml = await fetch_metadata_from_server(
            self._cfg["odata_url"],
            self._auth_header(),
            timeout=self._request_timeout,
        )
        if xml:
            self._metadata.parse_and_store(xml)
        else:
            log.warning("Не удалось загрузить $metadata — будет использован кэш при наличии")

    async def process_message(
        self,
        user_text: str,
        history: list[dict[str, str]],
        *,
        chat_id: int | None = None,
    ) -> AgentProcessResult:
        """Обработать сообщение пользователя через ODataPipeline."""
        if not self._pipeline or not self._error_handler:
            raise RuntimeError("ODataAgent не инициализирован")

        try:
            state = await self._pipeline.run(user_text, history, chat_id=chat_id)
            return AgentProcessResult(
                text=state.answer_html,
                history=state.history,
                attachments=state.attachments,
                chart_html=state.chart_html,
                skip_formatter=bool(state.attachments),
            )
        except Exception as exc:
            return self._error_handler.handle(exc, user_text, history)

    async def execute_page_with_ctx(
        self,
        ctx: dict[str, Any],
        skip: int,
        chat_id: int | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        """Выполнить запрос пагинации с явно переданным контекстом.

        Не зависит от chat_id — контекст пагинации управляется извне (через Chat).

        Args:
            ctx: контекст пагинации (entity, filter, select, ...).
            skip: значение $skip.
            chat_id: ID чата (передаётся в Step 2 для метрик).

        Returns:
            Кортеж (answer_html, pagination_context или None).
        """
        if not self._executor or not self._ai_service:
            raise RuntimeError("ODataAgent не инициализирован")

        entity = ctx["entity"]
        filter_expr = ctx.get("filter")
        select = ctx.get("select")
        orderby = ctx.get("orderby")
        top = ctx.get("top", self._default_top)
        expand = ctx.get("expand")

        prev_last_record: dict | None = None
        effective_skip = skip
        effective_top = top

        if skip > 0:
            effective_skip = skip - 1
            effective_top = top + 1

        try:
            records, total = await self._executor.execute(
                entity=entity,
                filter_expr=filter_expr,
                select=select,
                orderby=orderby,
                top=effective_top,
                skip=effective_skip,
                expand=expand,
            )
        except ODataError as e:
            log.error("Pagination OData error: %s", e)
            return f"❌ Ошибка запроса: {esc_html(str(e))}", None

        if skip > 0 and records:
            prev_last_record = records[0]
            records = records[1:]

        shown = len(records)
        answer = await self._ai_service.step2_format_response(
            user_text=f"Страница со смещением {skip}",
            records=records,
            total=total,
            entity=entity,
            shown=shown,
            skip=skip,
            prev_last_record=prev_last_record,
            chat_id=chat_id,
        )

        new_ctx = {
            **ctx,
            "skip": skip,
            "total": total,
            "shown": shown,
        }
        return answer, new_ctx

    async def execute_all_pages_with_ctx(
        self,
        ctx: dict[str, Any],
        user_text: str,
        *,
        chat_id: int | None = None,
        max_records: int = 500,
    ) -> str:
        """Загрузить все страницы результата и отформатировать одним ответом (для email).

        Вместо пагинации собирает все записи (до ``max_records``) и прогоняет Step 2 один раз.
        """
        if not self._executor or not self._ai_service:
            raise RuntimeError("ODataAgent не инициализирован")

        entity = ctx["entity"]
        filter_expr = ctx.get("filter")
        select = ctx.get("select")
        orderby = ctx.get("orderby")
        top = ctx.get("top", self._default_top)
        expand = ctx.get("expand")

        total = ctx.get("total", 0)
        skip = ctx.get("skip", 0)
        all_records: list[dict] = []

        while skip < total and len(all_records) < max_records:
            batch_top = min(top, max_records - len(all_records))
            try:
                records, total = await self._executor.execute(
                    entity=entity,
                    filter_expr=filter_expr,
                    select=select,
                    orderby=orderby,
                    top=batch_top,
                    skip=skip,
                    expand=expand,
                )
            except ODataError as e:
                log.error("Email fetch-all OData error at skip=%d: %s", skip, e)
                break

            if not records:
                break

            all_records.extend(records)
            skip += len(records)

            if len(records) < batch_top:
                break

        if not all_records:
            return "❌ Не удалось загрузить данные для полного ответа."

        log.info(
            "Email fetch-all: entity=%s, records=%d, total=%d",
            entity,
            len(all_records),
            total,
        )

        from bot.agents.odata.request_brief_advisor import RequestBriefAdvisor
        from bot.agents.odata.response_headline import apply_request_headline

        answer = await self._ai_service.step2_format_response(
            user_text=user_text,
            records=all_records,
            total=total,
            entity=entity,
            shown=len(all_records),
            skip=0,
            chat_id=chat_id,
        )
        brief = await RequestBriefAdvisor().advise(
            self._ai_service,
            user_query=user_text,
            chat_id=chat_id,
        )
        return apply_request_headline(answer, brief)

    def get_status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "initialized": self._initialized,
            "entities_count": len(self._metadata.entities),
            "mcp_connected": self._mcp_manager.is_connected() if self._mcp_manager else False,
            "model": self._model,
            "pipeline": self._pipeline is not None,
        }
