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
        log.info("STEP1 initial response: content=%r, tool_calls=%s",
                 (msg1.content or "")[:200] if msg1.content else None,
                 [tc.function.name for tc in msg1.tool_calls] if msg1.tool_calls else None)

        # Обработка function calls
        if msg1.tool_calls:
            tool_results = []
            for tc in msg1.tool_calls:
                fn = tc.function
                log.info("Tool call: %s(%s)", fn.name, fn.arguments[:300] if fn.arguments else "")
                result = self._handle_tool_call(fn.name, json.loads(fn.arguments))
                log.info("Tool result: %s", result[:300] if result else "")
                tool_results.append({"role": "tool", "tool_call_id": tc.id, "content": result})

            messages.append(msg1.model_dump())  # type: ignore[arg-type]
            messages.extend(tool_results)

            if self._rate_limiter:
                await self._rate_limiter.wait()

            resp1b = await self._step1_call_ai(messages, use_tools=True)
            msg1 = resp1b.choices[0].message
            log.info("STEP1 after tools: content=%r, tool_calls=%s",
                     (msg1.content or "")[:300] if msg1.content else None,
                     [tc.function.name for tc in msg1.tool_calls] if msg1.tool_calls else None)

            # Если модель снова вызвала инструменты — обработать рекурсивно
            if msg1.tool_calls:
                tool_results2 = []
                for tc in msg1.tool_calls:
                    fn = tc.function
                    log.info("Tool call (round 2): %s(%s)", fn.name, fn.arguments[:300] if fn.arguments else "")
                    result = self._handle_tool_call(fn.name, json.loads(fn.arguments))
                    log.info("Tool result (round 2): %s", result[:300] if result else "")
                    tool_results2.append({"role": "tool", "tool_call_id": tc.id, "content": result})

                messages.append(msg1.model_dump())  # type: ignore[arg-type]
                messages.extend(tool_results2)

                if self._rate_limiter:
                    await self._rate_limiter.wait()

                resp1c = await self._step1_call_ai(messages, use_tools=True)
                msg1 = resp1c.choices[0].message
                log.info("STEP1 after tools (round 2): content=%r",
                         (msg1.content or "")[:500] if msg1.content else None)

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
            history.append({"role": "assistant", "content": json.dumps({"entity": entity, "filter": query.get("filter"), "count": True, "explanation": query.get("explanation", "")}, ensure_ascii=False)})
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

        # Построить $expand для раскрытия ссылочных полей
        expand = self._build_expand_from_select(entity, select)

        # Проверить длину URL и при необходимости сократить $expand
        filter_expr = query.get("filter")
        expand = self._trim_expand_for_url_limit(
            self._cfg["odata_url"], entity, filter_expr, select, orderby, top, expand,
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
        )

        # Fallback: если 0 записей и фильтр содержит Number + любую дату — попробовать только Number
        if total == 0 and filter_expr and "Number" in filter_expr:
            # Убрать ВСЕ условия с datetime из фильтра (Date, ДатаУвольнения, и любые другие)
            fallback_filter = re.sub(
                r"\s*and\s+\w+\s+(eq|ge|le|gt|lt)\s+datetime'[^']*'",
                "", filter_expr
            )
            if fallback_filter != filter_expr:
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
                )

        # Fallback 2: если всё ещё 0 и Number с eq — попробовать substringof (OData v3)
        if total == 0 and filter_expr and "Number eq '" in filter_expr:
            number_match = re.search(r"Number eq '([^']*)'", filter_expr)
            if number_match:
                num = number_match.group(1)
                # Убрать префикс (например "ДМНВ-000007" → "000007")
                digits = re.sub(r'^[^\d]+', '', num)
                if digits and digits != num:
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
                    )

        # ---- Шаг 2: AI форматирует ответ ----
        answer = await self._ai_format_response(user_text, records, total, entity)

        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": json.dumps({"entity": entity, "filter": query.get("filter"), "select": query.get("select"), "explanation": query.get("explanation", "")}, ensure_ascii=False)})
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
        elif name == "search_entities":
            query = args.get("query", "")
            results = self._metadata.search_entities(query)
            if results:
                return json.dumps({"query": query, "results": results, "count": len(results)}, ensure_ascii=False)
            return f"По запросу '{query}' ничего не найдено. Попробуйте другое ключевое слово."
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

    # Максимальное количество навигационных свойств в $expand
    MAX_EXPAND_FIELDS = 15

    # Максимальная длина URL (IIS по умолчанию ~2048, берём с запасом)
    MAX_URL_LENGTH = 1800

    # Приоритеты раскрытия ссылочных полей через $expand.
    # Поля с высоким приоритетом (бизнес-суть) раскрываются первыми,
    # системные/подписи — последними (могут быть отброшены при лимите).
    _EXPAND_HIGH_PRIORITY: tuple[str, ...] = (
        "Организация", "Контрагент", "Сотрудник", "ФизическоеЛицо",
        "Номенклатура", "Склад", "Подразделение", "Должность",
        "Валюта", "СтатьяЗатрат", "СтатьяТКРФ", "ОснованиеУвольнения",
        "Основание", "Касса", "Банк", "Проект", "НаправлениеДеятельности",
        "ВидОперации", "ХозОперация", "ВидРасчета", "ВыходноеПособие",
        "Компенсация", "Статья",
    )
    _EXPAND_LOW_PRIORITY: tuple[str, ...] = (
        "Руководитель", "ГлавныйБухгалтер", "Бухгалтер",
        "РаботникКадровойСлужбы", "Исполнитель", "Ответственный",
        "ОтветственныйИсполнитель", "Рассчитал",
        "ДолжностьРуководителя", "ДолжностьГлавногоБухгалтера",
        "ДолжностьБухгалтера", "ДолжностьРаботникаКадровойСлужбы",
        "ДолжностьИсполнителя", "ДолжностьОтветственногоИсполнителя",
        "ИсправленныйДокумент",
    )

    def _expand_priority(self, nav_name: str) -> int:
        """Вернуть приоритет навигационного свойства (ниже = важнее)."""
        if nav_name in self._EXPAND_HIGH_PRIORITY:
            return 0
        # Проверяем частичные совпадения для high priority
        for pattern in self._EXPAND_HIGH_PRIORITY:
            if pattern in nav_name:
                return 0
        if nav_name in self._EXPAND_LOW_PRIORITY:
            return 2
        # Проверяем частичные совпадения для low priority
        for pattern in self._EXPAND_LOW_PRIORITY:
            if pattern in nav_name:
                return 2
        if nav_name.startswith("Удалить") or nav_name.startswith("Delete"):
            return 3
        return 1  # средний приоритет

    def _build_expand_from_select(self, entity: str, select: Optional[str]) -> Optional[str]:
        """Построить $expand на основе _Key полей из $select или метаданных.

        Для каждого поля вида «Имя_Key» добавляет навигационное свойство «Имя» в $expand.
        Если select равен None (все поля) — использует метаданные для поиска всех
        навигационных свойств сущности.

        Ограничивает количество expand-свойств до MAX_EXPAND_FIELDS,
        чтобы не превышать лимит длины URL в IIS.
        """
        nav_names: list[str] = []

        if select:
            raw = select[len("$select="):] if select.startswith("$select=") else select
            field_names = [f.strip() for f in raw.split(",") if f.strip()]
        else:
            # select=None → запрашиваем все поля; берём _Key из метаданных
            field_names = self._metadata.get_entity_fields(entity)

        for f in field_names:
            if f.endswith("_Key") and f not in (
                "Ref_Key", "DataVersion", "Predefined", "PredefinedDataName",
                "IsFolder", "LineNumber", "Parent_Key",
            ):
                nav_name = f[:-4]  # убрать суффикс _Key
                nav_names.append(nav_name)

        if not nav_names:
            return None

        # Сортировать по приоритету: бизнес-поля первые, системные/подписи — последние
        nav_names.sort(key=self._expand_priority)

        # Ограничить количество expand-свойств (с учётом приоритета)
        if len(nav_names) > self.MAX_EXPAND_FIELDS:
            log.info(
                "$expand для %s: %d свойств → ограничено до %d (с приоритетом)",
                entity, len(nav_names), self.MAX_EXPAND_FIELDS,
            )
            nav_names = nav_names[:self.MAX_EXPAND_FIELDS]

        expand = ",".join(nav_names)
        log.info("Auto $expand for %s: %s (from fields: %s)", entity, expand, field_names[:20])
        return expand

    def _estimate_url_length(
        self,
        odata_url: str,
        entity: str,
        filter_expr: Optional[str],
        select: Optional[str],
        orderby: Optional[str],
        top: int,
        expand: Optional[str],
    ) -> int:
        """Приблизительная оценка длины итогового URL OData-запроса."""
        from urllib.parse import quote
        base = f"{odata_url.rstrip('/')}/{quote(entity, safe='')}"
        params = [("$format", "json")]
        if filter_expr:
            params.append(("$filter", filter_expr))
        if select:
            raw = select[len("$select="):] if select.startswith("$select=") else select
            params.append(("$select", raw))
        if orderby:
            raw = orderby[len("$orderby="):] if orderby.startswith("$orderby=") else orderby
            params.append(("$orderby", raw))
        if top:
            params.append(("$top", str(top)))
        if expand:
            raw_expand = expand[len("$expand="):] if expand.startswith("$expand=") else expand
            params.append(("$expand", raw_expand))
        url_str = base + "?" + "&".join(f"{k}={quote(v, safe='')}" for k, v in params)
        return len(url_str)

    def _trim_expand_for_url_limit(
        self,
        odata_url: str,
        entity: str,
        filter_expr: Optional[str],
        select: Optional[str],
        orderby: Optional[str],
        top: int,
        expand: Optional[str],
    ) -> Optional[str]:
        """Обрезать $expand, если итоговый URL превышает MAX_URL_LENGTH."""
        if not expand:
            return expand

        current_url_len = self._estimate_url_length(
            odata_url, entity, filter_expr, select, orderby, top, expand,
        )
        if current_url_len <= self.MAX_URL_LENGTH:
            return expand

        # Прогрессивно сокращаем expand
        nav_list = expand.split(",")
        while len(nav_list) > 1:
            nav_list = nav_list[:-1]
            trimmed = ",".join(nav_list)
            current_url_len = self._estimate_url_length(
                odata_url, entity, filter_expr, select, orderby, top, trimmed,
            )
            if current_url_len <= self.MAX_URL_LENGTH:
                log.info(
                    "$expand сокращён: %d → %d свойств (URL %d → %d)",
                    len(expand.split(",")), len(nav_list),
                    self._estimate_url_length(
                        odata_url, entity, filter_expr, select, orderby, top, expand,
                    ),
                    current_url_len,
                )
                return trimmed

        # Даже одно свойство не помогает — убираем expand полностью
        log.warning("$expand убран полностью — URL слишком длинный (%d)", current_url_len)
        return None

    @staticmethod
    def _resolve_references(records: list[dict]) -> list[dict]:
        """Заменить _Key-GUID на представления из раскрытых навигационных свойств.

        Для каждого поля «Имя_Key» ищет пару «Имя» (dict) и берёт из неё
        Description / НаименованиеПолное / Code / Ref_Key как представление.
        Раскрытые dict-объекты удаляются, чтобы не засорять данные.
        """
        # Имена полей, которые всегда удаляются
        _SKIP_FIELDS = frozenset({
            "Ref_Key", "DataVersion", "DeletionMark", "Predefined",
            "PredefinedDataName", "IsFolder",
        })

        resolved = []
        for rec in records:
            new_rec: dict[str, Any] = {}
            # Собираем ключи навигационных свойств (dict-значения без _Key)
            nav_keys = {k for k, v in rec.items()
                        if isinstance(v, dict) and not k.endswith("_Key")}
            # Множество ключей для удаления: служебные + раскрытые объекты
            remove_keys = set(nav_keys) | _SKIP_FIELDS

            for key, value in rec.items():
                # Пропускаем служебные поля и раскрытые объекты
                if key in remove_keys:
                    continue
                # Заменяем _Key на представление
                if key.endswith("_Key"):
                    base = key[:-4]  # Организация_Key → Организация
                    if base in rec and isinstance(rec[base], dict):
                        obj = rec[base]
                        presentation = (
                            obj.get("Description")
                            or obj.get("НаименованиеПолное")
                            or obj.get("Code")
                            or obj.get("Ref_Key")
                        )
                        if presentation and presentation != obj.get("Ref_Key"):
                            new_rec[base] = presentation
                        else:
                            # Нет представления — не показываем
                            continue
                    else:
                        # Нет раскрытого объекта — не показываем GUID
                        continue
                else:
                    new_rec[key] = value

            resolved.append(new_rec)

        return resolved

    async def _ai_format_response(
        self,
        user_text: str,
        records: list[dict],
        total: int,
        entity: str,
    ) -> str:
        """Шаг 2: AI форматирует записи в HTML-ответ для Telegram."""
        resolved = self._resolve_references(records)
        sample = resolved[:30]
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