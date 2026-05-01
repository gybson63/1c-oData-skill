#!/usr/bin/env python3
"""OData-агент: обработка запросов к 1С через OData REST API.

Двухшаговая обработка:
  Шаг 1 — AI формирует OData-запрос (JSON) с помощью function calling
  Шаг 2 — AI форматирует результат для Telegram
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from openai import AsyncOpenAI, BadRequestError

from bot.utils import RateLimiter, esc_html

from bot.agents.base import BaseAgent
from .metadata import MetadataCache, fetch_metadata_from_server
from .odata_http import ODataError, execute_odata_query
from .prompts import (
    ODATA_REFERENCE,
    STEP1_SYSTEM,
    STEP2_SYSTEM,
    make_step1_tools,
)

log = logging.getLogger(__name__)

HISTORY_MAX_TURNS = 10


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
        self._cfg = {**global_config, **agent_config}
        self._model = self._cfg.get("ai_model", "gpt-4o-mini")
        self._metadata = MetadataCache(cache_dir)

        # AI client
        self._ai_client = AsyncOpenAI(
            api_key=self._cfg["ai_api_key"],
            base_url=self._cfg.get("ai_base_url"),
            max_retries=0,
        )

        # Rate limiter
        rpm = self._cfg.get("ai_rpm", 20)
        self._rate_limiter = RateLimiter(rpm=rpm)

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
        xml = await fetch_metadata_from_server(self._cfg["odata_url"], self._auth_header())
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
            return answer, history[-(HISTORY_MAX_TURNS * 2):]
        except QueryError as e:
            answer = f"⚠️ {esc_html(str(e))}"
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": answer})
            return answer, history[-(HISTORY_MAX_TURNS * 2):]
        except Exception:
            log.exception("Unexpected error in ODataAgent")
            answer = "💥 Произошла непредвиденная ошибка. Попробуйте позже."
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": answer})
            return answer, history[-(HISTORY_MAX_TURNS * 2):]

    def _is_tool_use_error(self, exc: BadRequestError) -> bool:
        """Проверить, связана ли ошибка с отсутствием поддержки tool use."""
        msg = str(exc).lower()
        return "tool use" in msg or "tool_choice" in msg or "functions" in msg

    def _build_step1_prompt(self) -> str:
        """Построить системный промпт для Шага 1 (с учетом поддержки инструментов)."""
        base = STEP1_SYSTEM.format(metadata=self._metadata.format_entity_list())
        if not self._tools_supported:
            # Встроить справочник OData прямо в промпт, т.к. инструменты недоступны
            ref_lines = ["\n\n--- СПРАВОЧНИК ODATA (инструменты недоступны, используй напрямую) ---"]
            for topic, text in ODATA_REFERENCE.items():
                ref_lines.append(f"\n[{topic}]\n{text}")
            base += "\n".join(ref_lines)
        return base

    async def _step1_call_ai(
        self,
        messages: list[dict],
        use_tools: bool,
    ):
        """Вызов AI для Шага 1 — с инструментами или без."""
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,  # type: ignore[arg-type]
            "temperature": 0.1,
        }
        if use_tools and self._tools:
            kwargs["tools"] = self._tools
            kwargs["tool_choice"] = "auto"

        return await self._ai_client.chat.completions.create(**kwargs)

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
                # Перестроить промпт со встроенным справочником
                system_prompt = self._build_step1_prompt()
                messages[0] = {"role": "system", "content": system_prompt}
                if self._rate_limiter:
                    await self._rate_limiter.wait()
                resp1 = await self._step1_call_ai(messages, use_tools=False)
            else:
                raise

        msg1 = resp1.choices[0].message

        # Обработка function calls
        if msg1.tool_calls:
            tool_results = []
            for tc in msg1.tool_calls:
                fn = tc.function
                result = self._handle_tool_call(fn.name, json.loads(fn.arguments))
                tool_results.append({"role": "tool", "tool_call_id": tc.id, "content": result})

            messages.append(msg1.model_dump())  # type: ignore[arg-type]
            messages.extend(tool_results)

            if self._rate_limiter:
                await self._rate_limiter.wait()

            resp1b = await self._step1_call_ai(messages, use_tools=True)
            msg1 = resp1b.choices[0].message

        # Извлечь JSON из ответа
        content = msg1.content or ""
        query = self._extract_json(content)
        if not query:
            raise QueryError(
                f"Не удалось разобрать запрос. Попробуйте переформулировать.\n\n"
                f"<pre>{esc_html(content[:500])}</pre>"
            )

        entity = query.get("entity", "")
        if not entity:
            raise QueryError("Не указана сущность (entity) в запросе.")

        do_count = query.get("count", False)

        if do_count:
            # Подсчёт записей
            records, total = await execute_odata_query(
                odata_url=self._cfg["odata_url"],
                auth_header=auth,
                entity=entity,
                filter_expr=query.get("filter"),
                count=True,
            )
            answer = f"<b>📊 Количество</b> <i>{esc_html(entity)}</i>: <code>{total}</code>"
            if query.get("explanation"):
                answer += f"\n<i>{esc_html(query['explanation'])}</i>"

            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": json.dumps({"entity": entity, "count": True, "explanation": query.get("explanation", "")}, ensure_ascii=False)})
            return answer, history[-(HISTORY_MAX_TURNS * 2):]

        # ---- Обычный запрос ----
        top = min(int(query.get("top") or 20), 50)

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
            if select:
                raw_select = select[len("$select="):] if select.startswith("$select=") else select
                valid = [f.strip() for f in raw_select.split(",") if f.strip() in fields]
                select = ",".join(valid) if valid else None
                if select != raw_select:
                    log.info("$select скорректирован: %s → %s", raw_select, select)
            if orderby:
                raw_orderby = orderby[len("$orderby="):] if orderby.startswith("$orderby=") else orderby
                field_name = raw_orderby.split()[0]
                if field_name not in fields:
                    log.info("$orderby '%s' не найден в полях, убираем", field_name)
                    orderby = None

        # Выполнение OData-запроса
        records, total = await execute_odata_query(
            odata_url=self._cfg["odata_url"],
            auth_header=auth,
            entity=entity,
            filter_expr=query.get("filter"),
            select=select,
            orderby=orderby,
            top=top,
        )

        # ---- Шаг 2: AI форматирует ответ ----
        answer = await self._ai_format_response(user_text, records, total, entity)

        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": json.dumps({"entity": entity, "explanation": query.get("explanation", "")}, ensure_ascii=False)})
        return answer, history[-(HISTORY_MAX_TURNS * 2):]

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
        return f"Неизвестный инструмент: {name}"

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
        # Ищем первую { и затем находим парную } подсчётом глубины
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
                            break  # не JSON — не ищем дальше

        # Попробовать весь текст как JSON
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    async def _ai_format_response(
        self,
        user_text: str,
        records: list[dict],
        total: int,
        entity: str,
    ) -> str:
        """Шаг 2: AI форматирует записи в HTML-ответ для Telegram."""
        sample = records[:30]
        data_str = json.dumps(sample, ensure_ascii=False, indent=2)
        if len(data_str) > 8000:
            data_str = data_str[:8000] + "\n... (данные сокращены)"

        messages = [
            {"role": "system", "content": STEP2_SYSTEM},
            {"role": "user", "content": f"Вопрос: {user_text}\n\nСущность: {entity}\nВсего записей: {total}\n\nДанные:\n{data_str}"},
        ]

        if self._rate_limiter:
            await self._rate_limiter.wait()

        resp = await self._ai_client.chat.completions.create(
            model=self._model,
            messages=messages,  # type: ignore[arg-type]
            temperature=0.3,
        )
        return resp.choices[0].message.content or "Не удалось сформировать ответ."

    # -- status --

    def get_status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "initialized": self._initialized,
            "entities_count": len(self._metadata.entities),
            "mcp_connected": self._mcp_manager.is_connected() if self._mcp_manager else False,
            "model": self._model,
        }