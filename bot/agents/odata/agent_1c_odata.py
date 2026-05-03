#!/usr/bin/env python3
"""OData-агент: обработка запросов к 1С через OData REST API.

Двухшаговая обработка:
  Шаг 1 — AI формирует OData-запрос (JSON) с помощью function calling
  Шаг 2 — AI форматирует результат для Telegram

Координация модулей:
  - :mod:`bot.agents.odata.metadata` — кэш и загрузка $metadata
  - :mod:`bot.agents.odata.odata_http` — выполнение OData-запросов
  - :mod:`bot.agents.odata.query_builder` — построение $expand, URL-лимиты
  - :mod:`bot.agents.odata.response_parser` — разрешение ссылок, подготовка данных
  - :mod:`bot.agents.odata.prompts` — промпты и инструменты для AI
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from openai import AsyncOpenAI, BadRequestError

from bot.config import get_settings
from bot.utils import RateLimiter, esc_html

from bot.agents.base import BaseAgent
from bot_lib.exceptions import ODataError, AIError, AIRateLimitError, AIResponseError
from .metadata import MetadataCache, fetch_metadata_from_server
from .odata_http import execute_odata_query
from .query_builder import build_expand, trim_expand_for_url_limit
from .response_parser import resolve_references
from .prompts import (
    ODATA_REFERENCE,
    STEP1_SYSTEM,
    STEP2_SYSTEM,
    make_step1_tools,
)

log = logging.getLogger(__name__)

# Значения по умолчанию (переопределяются из конфигурации)
_DEFAULT_HISTORY_MAX_TURNS = 10
_DEFAULT_DEFAULT_TOP = 20
_DEFAULT_MAX_TOP = 50
_DEFAULT_MAX_EXPAND_FIELDS = 15
_DEFAULT_MAX_URL_LENGTH = 1800
_DEFAULT_MAX_SAMPLE_RECORDS = 30
_DEFAULT_MAX_DATA_LENGTH = 8000
_DEFAULT_STEP1_TEMPERATURE = 0.1
_DEFAULT_STEP2_TEMPERATURE = 0.3


class QueryError(Exception):
    """Ошибка разбора запроса."""
    pass


class ODataAgent(BaseAgent):
    """Агент для работы с 1С OData."""

    name = "odata"

    def __init__(self) -> None:
        super().__init__()
        self._ai_client: Optional[AsyncOpenAI] = None
        self._metadata = MetadataCache()
        self._mcp_manager = None
        self._cfg: dict[str, Any] = {}
        self._model: str = ""
        self._rate_limiter: Optional[RateLimiter] = None
        self._tools: list[dict] = []
        self._tools_map: dict[str, Any] = {}
        self._tools_supported: bool = True
        # Настраиваемые параметры (из конфигурации)
        self._history_max_turns: int = _DEFAULT_HISTORY_MAX_TURNS
        self._default_top: int = _DEFAULT_DEFAULT_TOP
        self._max_top: int = _DEFAULT_MAX_TOP
        self._max_expand_fields: int = _DEFAULT_MAX_EXPAND_FIELDS
        self._max_url_length: int = _DEFAULT_MAX_URL_LENGTH
        self._max_sample_records: int = _DEFAULT_MAX_SAMPLE_RECORDS
        self._max_data_length: int = _DEFAULT_MAX_DATA_LENGTH
        self._step1_temperature: float = _DEFAULT_STEP1_TEMPERATURE
        self._step2_temperature: float = _DEFAULT_STEP2_TEMPERATURE
        self._request_timeout: int = 60
        self._metadata_cache_seconds: int = 86400

    # -- helpers --

    def _auth_header(self) -> str:
        import base64
        u, p = self._cfg["odata_user"], self._cfg["odata_password"]
        token = base64.b64encode(f"{u}:{p}".encode()).decode()
        return f"Basic {token}"

    # -- lifecycle --

    async def initialize(
        self,
        agent_config: dict[str, Any],
        global_config: dict[str, Any],
        cache_dir: str = ".cache",
        env_file: str = "env.json",
    ) -> None:
        # Мерж agent_config + global_config для обратной совместимости
        self._cfg = {**global_config, **agent_config}

        # Типизированные настройки через Pydantic Settings
        settings = get_settings()
        ai = settings.ai
        odata = settings.odata_query

        self._model = ai.model
        self._metadata = MetadataCache(cache_dir, cache_seconds=odata.metadata_cache_seconds)
        self._metadata_cache_seconds = odata.metadata_cache_seconds

        # AI client
        self._ai_client = AsyncOpenAI(
            api_key=ai.api_key,
            base_url=ai.base_url,
            max_retries=0,
        )

        # Rate limiter
        self._rate_limiter = RateLimiter(rpm=ai.rpm)

        # Настраиваемые параметры OData — из типизированной конфигурации
        self._history_max_turns = settings.history_max_turns
        self._default_top = odata.default_top
        self._max_top = odata.max_top
        self._max_expand_fields = odata.max_expand_fields
        self._max_url_length = odata.max_url_length
        self._request_timeout = odata.request_timeout
        self._max_sample_records = odata.max_sample_records
        self._max_data_length = odata.max_data_length
        self._step1_temperature = ai.temperature
        self._step2_temperature = ai.temperature_step2

        # MCP
        mcp_config = agent_config.get("mcp_servers", {})
        if mcp_config:
            from bot.mcp_client import MCPClientManager
            self._mcp_manager = MCPClientManager()
            await self._mcp_manager.connect_all(mcp_config)
            if self._mcp_manager.is_connected():
                status = self._mcp_manager.get_status()
                for srv, info in status.items():
                    log.info("ODataAgent MCP [%s]: transport=%s, tools=%s", srv, info["transport"], info["tools"])

        # Metadata
        loaded = self._metadata.load_from_disk()
        if not loaded:
            await self._load_metadata(force=True)

        # AI tools
        ref_keys = list(ODATA_REFERENCE.keys())
        self._tools = make_step1_tools(ref_keys)
        self._tools_map = {t["function"]["name"]: t["function"] for t in self._tools}

        self._initialized = True
        log.info("ODataAgent инициализирован (сущностей: %d)", len(self._metadata.entities))

    async def shutdown(self) -> None:
        if self._mcp_manager:
            await self._mcp_manager.disconnect_all()
            self._mcp_manager = None
        self._initialized = False
        log.info("ODataAgent остановлен")

    async def refresh(self) -> None:
        await self._load_metadata(force=True)
        log.info("ODataAgent: метаданные обновлены (%d сущностей)", len(self._metadata.entities))

    # -- metadata loading --

    async def _load_metadata(self, force: bool = False) -> None:
        if not force and self._metadata.is_loaded:
            return
        xml = await fetch_metadata_from_server(self._cfg["odata_url"], self._auth_header(), timeout=self._request_timeout)
        if xml:
            self._metadata.parse_and_store(xml)
        else:
            log.warning("Не удалось загрузить $metadata — будет использован кэш при наличии")

    # -- processing --

    async def process_message(
        self,
        user_text: str,
        history: list[dict[str, str]],
    ) -> tuple[str, list[dict[str, str]]]:
        """Основной метод обработки сообщения.

        Returns:
            (answer_html, updated_history)
        """
        try:
            return await self._process_internal(user_text, history)
        except ODataError as e:
            log.error("OData error: %s (status=%s)", e, e.status_code)
            if e.status_code == 401:
                answer = "🔒 <b>Ошибка авторизации в 1С.</b> Проверьте логин и пароль."
            elif e.status_code == 404:
                answer = "🔍 <b>Объект не найден в OData.</b> Возможно, он не опубликован в базе 1С."
            elif e.status_code >= 500:
                answer = "🛑 <b>Ошибка сервера 1С.</b> Попробуйте позже."
            else:
                answer = f"❌ <b>Не удалось подключиться к 1С:</b> {esc_html(str(e))}"
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": answer})
            return answer, history[-(self._history_max_turns * 2):]
        except AIRateLimitError as e:
            log.warning("AI rate limit: %s", e)
            answer = "⏳ <b>Превышен лимит запросов к AI.</b> Подождите минуту и повторите."
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": answer})
            return answer, history[-(self._history_max_turns * 2):]
        except AIError as e:
            log.error("AI error: %s", e)
            answer = f"🤖 <b>Ошибка AI-сервиса:</b> {esc_html(str(e))}"
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": answer})
            return answer, history[-(self._history_max_turns * 2):]
        except QueryError as e:
            answer = f"⚠️ {esc_html(str(e))}"
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": answer})
            return answer, history[-(self._history_max_turns * 2):]
        except Exception:
            log.exception("Unexpected error in ODataAgent")
            answer = "💥 Произошла непредвиденная ошибка. Попробуйте позже."
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": answer})
            return answer, history[-(self._history_max_turns * 2):]

    # -- AI interaction helpers --

    def _is_tool_use_error(self, exc: BadRequestError) -> bool:
        """Проверить, связана ли ошибка с отсутствием поддержки tool use."""
        msg = str(exc).lower()
        return "tool use" in msg or "tool_choice" in msg or "functions" in msg

    def _build_step1_prompt(self) -> str:
        """Построить системный промпт для Шага 1 (с учетом поддержки инструментов)."""
        base = STEP1_SYSTEM.format(metadata=self._metadata.format_entity_list())
        if not self._tools_supported:
            ref_lines = ["\n\n--- СПРАВОЧНИК ODATA (инструменты недоступны, используй напрямую) ---"]
            for topic, text in ODATA_REFERENCE.items():
                ref_lines.append(f"\n[{topic}]\n{text}")
            base += "\n".join(ref_lines)
        return base

    @staticmethod
    def _wrap_ai_error(exc: Exception) -> AIError:
        """Обернуть ошибку AI-провайдера в типизированное исключение."""
        msg = str(exc).lower()
        if "429" in msg or "rate" in msg or "limit" in msg:
            return AIRateLimitError(f"Превышен лимит запросов: {exc}")
        return AIError(f"Ошибка AI-сервиса: {exc}")

    async def _step1_call_ai(
        self,
        messages: list[dict],
        use_tools: bool,
    ):
        """Вызов AI для Шага 1 — с инструментами или без."""
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,  # type: ignore[arg-type]
            "temperature": self._step1_temperature,
        }
        if use_tools and self._tools:
            kwargs["tools"] = self._tools
            kwargs["tool_choice"] = "auto"

        try:
            return await self._ai_client.chat.completions.create(**kwargs)  # type: ignore[union-attr]
        except BadRequestError as exc:
            raise
        except Exception as exc:
            raise self._wrap_ai_error(exc) from exc

    # -- tool call handling --

    def _handle_tool_call(self, name: str, args: dict) -> str:
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

    # -- JSON extraction --

    @staticmethod
    def _extract_json(text: str) -> Optional[dict]:
        """Извлечь JSON из текста ответа AI."""
        text = text.strip()
        # Убрать markdown-обёртки
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        # Найти JSON-объект (с поддержкой вложенных фигурных скобок)
        start = text.find("{")
        if start != -1:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start : i + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            break

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    # -- core processing --

    async def _process_internal(
        self,
        user_text: str,
        history: list[dict[str, str]],
    ) -> tuple[str, list[dict[str, str]]]:
        """Внутренняя реализация двухшаговой обработки."""
        auth = self._auth_header()

        # ---- Шаг 1: AI формирует OData-запрос ----
        system_prompt = self._build_step1_prompt()

        messages = [{"role": "system", "content": system_prompt}] + list(history) + [{"role": "user", "content": user_text}]

        # Rate limit
        if self._rate_limiter:
            await self._rate_limiter.wait()

        use_tools = self._tools_supported
        try:
            resp1 = await self._step1_call_ai(messages, use_tools=use_tools)
        except BadRequestError as e:
            if use_tools and self._is_tool_use_error(e):
                log.warning("Модель %s не поддерживает tool use, повтор без инструментов", self._model)
                self._tools_supported = False
                system_prompt = self._build_step1_prompt()
                messages[0] = {"role": "system", "content": system_prompt}
                if self._rate_limiter:
                    await self._rate_limiter.wait()
                resp1 = await self._step1_call_ai(messages, use_tools=False)
            else:
                raise

        msg1 = resp1.choices[0].message
        log.info("STEP1 initial response: content=%r, tool_calls=%s",
                 (msg1.content or "")[:200] if msg1.content else None,
                 [tc.function.name for tc in msg1.tool_calls] if msg1.tool_calls else None)

        # Обработка function calls (до 2 раундов)
        msg1 = await self._resolve_tool_calls(messages, msg1)

        # Извлечь JSON из ответа
        content = msg1.content or ""
        log.info("STEP1 final content (len=%d): %s", len(content), content[:1000])
        query = self._extract_json(content)
        if not query:
            log.warning("Не удалось извлечь JSON из ответа AI. Полный ответ:\n%s", content)
            raise QueryError(
                f"Не удалось разобрать запрос. Попробуйте переформулировать.\n\n"
                f"<pre>{esc_html(content[:500])}</pre>"
            )

        log.info("STEP1 parsed query: %s", json.dumps(query, ensure_ascii=False)[:500])

        entity = query.get("entity", "")
        if not entity:
            raise QueryError("Не указана сущность (entity) в запросе.")

        do_count = query.get("count", False)

        if do_count:
            return await self._handle_count_query(user_text, history, query, entity, auth)

        # ---- Обычный запрос ----
        return await self._handle_data_query(user_text, history, query, entity, auth)

    async def _resolve_tool_calls(self, messages: list[dict], msg1) -> Any:
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
                result = self._handle_tool_call(fn.name, json.loads(fn.arguments))
                log.info("Tool result (round %d): %s", round_num, result[:300] if result else "")
                tool_results.append({"role": "tool", "tool_call_id": tc.id, "content": result})

            messages.append(msg1.model_dump())  # type: ignore[arg-type]
            messages.extend(tool_results)

            if self._rate_limiter:
                await self._rate_limiter.wait()

            resp = await self._step1_call_ai(messages, use_tools=True)
            msg1 = resp.choices[0].message
            log.info("STEP1 after tools (round %d): content=%r",
                     round_num, (msg1.content or "")[:500] if msg1.content else None)

        return msg1

    async def _handle_count_query(
        self,
        user_text: str,
        history: list[dict[str, str]],
        query: dict,
        entity: str,
        auth: str,
    ) -> tuple[str, list[dict[str, str]]]:
        """Обработать запрос на подсчёт записей."""
        records, total = await execute_odata_query(
            odata_url=self._cfg["odata_url"],
            auth_header=auth,
            entity=entity,
            filter_expr=query.get("filter"),
            count=True,
            request_timeout=self._request_timeout,
        )
        answer = f"<b>📊 Количество</b> <i>{esc_html(entity)}</i>: <code>{total}</code>"
        if query.get("explanation"):
            answer += f"\n<i>{esc_html(query['explanation'])}</i>"

        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": json.dumps({"entity": entity, "filter": query.get("filter"), "count": True, "explanation": query.get("explanation", "")}, ensure_ascii=False)})
        return answer, history[-(self._history_max_turns * 2):]

    async def _handle_data_query(
        self,
        user_text: str,
        history: list[dict[str, str]],
        query: dict,
        entity: str,
        auth: str,
    ) -> tuple[str, list[dict[str, str]]]:
        """Обработать запрос данных сущности."""
        top = min(int(query.get("top") or self._default_top), self._max_top)

        # Валидация $select
        select = query.get("select")
        if isinstance(select, list):
            select = ",".join(str(s) for s in select)
        orderby = query.get("orderby")
        if isinstance(orderby, list):
            orderby = ",".join(str(s) for s in orderby)

        fields = self._metadata.get_entity_fields(entity)
        if fields:
            log.info("Fields for %s: %s", entity, fields)
            select = self._validate_select(fields, select)
            orderby = self._validate_orderby(fields, orderby)

        # Построить $expand для раскрытия ссылочных полей
        expand = build_expand(entity, select, fields, self._max_expand_fields)

        # Проверить длину URL и при необходимости сократить $expand
        filter_expr = query.get("filter")
        expand = trim_expand_for_url_limit(
            self._cfg["odata_url"], entity, filter_expr, select, orderby, top, expand,
            max_url_length=self._max_url_length,
        )

        # Выполнение OData-запроса
        records, total = await execute_odata_query(
            odata_url=self._cfg["odata_url"],
            auth_header=auth,
            entity=entity,
            filter_expr=filter_expr,
            select=select,
            orderby=orderby,
            top=top,
            expand=expand,
            request_timeout=self._request_timeout,
        )

        # Fallback: если 0 записей и фильтр содержит Number + любую дату
        records, total = await self._fallback_date_filter(
            records, total, entity, filter_expr, select, orderby, top, expand, auth,
        )

        # Fallback 2: substringof (OData v3)
        records, total = await self._fallback_substringof(
            records, total, entity, filter_expr, select, orderby, top, expand, auth,
        )

        # ---- Шаг 2: AI форматирует ответ ----
        answer = await self._ai_format_response(user_text, records, total, entity)

        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": json.dumps({"entity": entity, "filter": query.get("filter"), "select": query.get("select"), "explanation": query.get("explanation", "")}, ensure_ascii=False)})
        return answer, history[-(self._history_max_turns * 2):]

    # -- query validation helpers --

    @staticmethod
    def _validate_select(fields: list[str], select: Optional[str]) -> Optional[str]:
        """Скорректировать $select, оставив только существующие поля."""
        if not select:
            return select
        raw_select = select[len("$select="):] if select.startswith("$select=") else select
        valid = [f.strip() for f in raw_select.split(",") if f.strip() in fields]
        result = ",".join(valid) if valid else None
        if result != raw_select:
            log.info("$select скорректирован: %s → %s", raw_select, result)
        return result

    @staticmethod
    def _validate_orderby(fields: list[str], orderby: Optional[str]) -> Optional[str]:
        """Скорректировать $orderby, проверив что поле существует."""
        if not orderby:
            return orderby
        raw_orderby = orderby[len("$orderby="):] if orderby.startswith("$orderby=") else orderby
        field_name = raw_orderby.split()[0]
        if field_name not in fields:
            log.info("$orderby '%s' не найден в полях, убираем", field_name)
            return None
        return orderby

    # -- fallback strategies --

    async def _fallback_date_filter(
        self,
        records: list[dict],
        total: int,
        entity: str,
        filter_expr: Optional[str],
        select: Optional[str],
        orderby: Optional[str],
        top: int,
        expand: Optional[str],
        auth: str,
    ) -> tuple[list[dict], int]:
        """Fallback: убрать условия с datetime если 0 записей и фильтр содержит Number."""
        if total != 0 or not filter_expr or "Number" not in filter_expr:
            return records, total

        fallback_filter = re.sub(
            r"\s*and\s+\w+\s+(eq|ge|le|gt|lt)\s+datetime'[^']*'",
            "", filter_expr,
        )
        if fallback_filter == filter_expr:
            return records, total

        log.info("Fallback 1: retry without any date filter: %s", fallback_filter)
        records, total = await execute_odata_query(
            odata_url=self._cfg["odata_url"],
            auth_header=auth,
            entity=entity,
            filter_expr=fallback_filter,
            select=select,
            orderby=orderby,
            top=top,
            expand=expand,
            request_timeout=self._request_timeout,
        )
        return records, total

    async def _fallback_substringof(
        self,
        records: list[dict],
        total: int,
        entity: str,
        filter_expr: Optional[str],
        select: Optional[str],
        orderby: Optional[str],
        top: int,
        expand: Optional[str],
        auth: str,
    ) -> tuple[list[dict], int]:
        """Fallback 2: попробовать substringof если 0 записей с Number eq."""
        if total != 0 or not filter_expr or "Number eq '" not in filter_expr:
            return records, total

        number_match = re.search(r"Number eq '([^']*)'", filter_expr)
        if not number_match:
            return records, total

        num = number_match.group(1)
        digits = re.sub(r'^[^\d]+', '', num)
        if not digits or digits == num:
            return records, total

        contains_filter = f"DeletionMark eq false and substringof('{digits}', Number)"
        log.info("Fallback 2: retry with substringof('%s', Number): %s", digits, contains_filter)
        records, total = await execute_odata_query(
            odata_url=self._cfg["odata_url"],
            auth_header=auth,
            entity=entity,
            filter_expr=contains_filter,
            select=select,
            orderby=orderby,
            top=top,
            expand=expand,
            request_timeout=self._request_timeout,
        )
        return records, total

    # -- AI formatting (Step 2) --

    async def _ai_format_response(
        self,
        user_text: str,
        records: list[dict],
        total: int,
        entity: str,
    ) -> str:
        """Шаг 2: AI форматирует записи в HTML-ответ для Telegram."""
        resolved = resolve_references(records)
        sample = resolved[:self._max_sample_records]
        data_str = json.dumps(sample, ensure_ascii=False, indent=2)
        if len(data_str) > self._max_data_length:
            data_str = data_str[:self._max_data_length] + "\n... (данные сокращены)"

        messages = [
            {"role": "system", "content": STEP2_SYSTEM},
            {"role": "user", "content": f"Вопрос: {user_text}\n\nСущность: {entity}\nВсего записей: {total}\n\nДанные:\n{data_str}"},
        ]

        if self._rate_limiter:
            await self._rate_limiter.wait()

        try:
            resp = await self._ai_client.chat.completions.create(  # type: ignore[union-attr]
                model=self._model,
                messages=messages,  # type: ignore[arg-type]
                temperature=self._step2_temperature,
            )
        except Exception as exc:
            raise self._wrap_ai_error(exc) from exc

        content = resp.choices[0].message.content
        if not content:
            raise AIResponseError("AI вернул пустой ответ на шаге форматирования")
        return content

    # -- status --

    def get_status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "initialized": self._initialized,
            "entities_count": len(self._metadata.entities),
            "mcp_connected": self._mcp_manager.is_connected() if self._mcp_manager else False,
            "model": self._model,
        }