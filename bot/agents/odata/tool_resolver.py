#!/usr/bin/env python3
"""Цепочка разрешения tool calls (Chain of Responsibility).

4 уровня fallback для получения OData-запроса от AI:

1. **NativeFunctionCallResolver** — стандартный OpenAI function calling
2. **InlineJsonResolver** — модель вернула tool call как JSON в content
3. **TextToolCallResolver** — модель вернула текстовый вызов (regex)
4. **AutoSearchResolver** — автоматический поиск сущности по ключевому слову

Каждый резолвер пытается обработать ответ AI и вернуть :class:`ODataQuery`.
Если не удалось — передаёт следующему в цепочке.

Использование::

    chain = NativeFunctionCallResolver(
        InlineJsonResolver(
            TextToolCallResolver(
                AutoSearchResolver(metadata=metadata)
            )
        )
    )
    query = await chain.resolve(state, ai_service)
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bot.agents.odata.state import ODataQuery, ODataState

log = logging.getLogger(__name__)


class ToolResolver(ABC):
    """Базовый класс для цепочки разрешения tool calls."""

    def __init__(self, next_resolver: ToolResolver | None = None) -> None:
        self._next = next_resolver

    async def resolve(
        self,
        state: ODataState,
        ai_service: Any,
    ) -> ODataQuery | None:
        """Попробовать разрешить запрос, иначе передать дальше по цепочке."""
        result = await self._try_resolve(state, ai_service)
        if result is not None:
            return result
        if self._next:
            return await self._next.resolve(state, ai_service)
        return None

    @abstractmethod
    async def _try_resolve(
        self,
        state: ODataState,
        ai_service: Any,
    ) -> ODataQuery | None:
        """Попытаться извлечь ODataQuery из текущего состояния."""
        ...


class NativeFunctionCallResolver(ToolResolver):
    """Уровень 1: обработка стандартных OpenAI function calls.

    AI возвращает ``tool_calls`` в ответе — выполняем инструменты и
    повторяем запрос с результатами (до 2 раундов).
    """

    async def _try_resolve(
        self,
        state: ODataState,
        ai_service: Any,
    ) -> ODataQuery | None:
        # Этот резолвер не извлекает query напрямую из content,
        # а работает через ai_service.resolve_tool_calls(), который
        # мутирует state.ai_messages и state.ai_response_content.
        # Логика function calling уже встроена в ai_service.step1_build_query().
        # Если content содержит JSON — пробуем распарсить.
        from bot.agents.odata.state import ODataQuery

        content = state.ai_response_content
        if not content:
            return None

        query_dict = _extract_json(content)
        if query_dict and query_dict.get("entity"):
            return ODataQuery.from_dict(query_dict)
        return None


class InlineJsonResolver(ToolResolver):
    """Уровень 2: модель вернула tool call как JSON-объект в content.

    Некоторые модели вместо function calling возвращают::
        {"name": "search_entities", "arguments": {"query": "..."}}
    """

    _TOOL_NAMES = frozenset({"odata_reference", "get_entity_fields", "search_entities"})

    async def _try_resolve(
        self,
        state: ODataState,
        ai_service: Any,
    ) -> ODataQuery | None:
        from bot.agents.odata.state import ODataQuery

        content = state.ai_response_content
        if not content:
            return None

        parsed = _extract_json(content)
        if not parsed or not isinstance(parsed, dict):
            return None

        # Это не inline tool call если есть entity (это OData-запрос)
        if "entity" in parsed:
            return None

        tool_name = parsed.get("name") or parsed.get("function")
        if tool_name not in self._TOOL_NAMES or not isinstance(parsed.get("arguments"), dict):
            return None

        log.warning("Обнаружен inline tool call: %s(%s)", tool_name, parsed["arguments"])

        # Выполнить инструмент через ai_service
        result = ai_service.handle_tool_call(tool_name, parsed["arguments"])
        log.info("Inline tool result: %s", result[:300] if result else "")

        # Повторить запрос с результатом инструмента
        tool_msg = (
            f"[Результат инструмента {tool_name}]: {result}\n\n"
            f"Теперь построй OData-запрос JSON для: {state.user_text}"
        )
        state.ai_messages.append({"role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)})
        state.ai_messages.append({"role": "user", "content": tool_msg})

        resp = await ai_service.step1_call_ai(state.ai_messages, use_tools=state.tools_supported)
        content = resp.choices[0].message.content or ""

        # Обработать возможные tool_calls от повторного запроса
        msg = resp.choices[0].message
        if msg.tool_calls:
            msg = await ai_service.resolve_tool_calls(state.ai_messages, msg)
            content = msg.content or ""

        query_dict = _extract_json(content)
        if query_dict and query_dict.get("entity"):
            return ODataQuery.from_dict(query_dict)

        # Сохранить content для следующих резолверов
        state.ai_response_content = content
        return None


class TextToolCallResolver(ToolResolver):
    """Уровень 3: модель вернула текстовый вызов инструмента.

    Формат::
        search_entities(query='Организации')
        get_entity_fields(entity_name='Catalog_Организации')
    """

    _TEXT_TOOL_RE = re.compile(
        r'\b(search_entities|get_entity_fields|odata_reference)\s*\(\s*'
        r'(\w+)\s*=\s*[\'"]([^\'"]*)[\'"]'
        r'\s*\)'
    )
    _TOOL_NAMES = frozenset({"odata_reference", "get_entity_fields", "search_entities"})

    async def _try_resolve(
        self,
        state: ODataState,
        ai_service: Any,
    ) -> ODataQuery | None:
        from bot.agents.odata.state import ODataQuery

        content = state.ai_response_content
        if not content:
            return None

        match = self._TEXT_TOOL_RE.search(content)
        if not match:
            return None

        tool_name = match.group(1)
        param_name = match.group(2)
        param_value = match.group(3)

        if tool_name not in self._TOOL_NAMES:
            return None

        tool_args = {param_name: param_value}
        log.info("Распознан текстовый tool call: %s(%s)", tool_name, tool_args)

        # Выполнить инструмент
        result = ai_service.handle_tool_call(tool_name, tool_args)
        log.info("Text tool result: %s", result[:500] if result else "")

        # Повторить запрос с результатом
        tool_msg = (
            f"[Результат инструмента {tool_name}({tool_args})]: {result}\n\n"
            f"Теперь СРАЗУ построй OData-запрос JSON для: {state.user_text}\n"
            f"⚠️ ТОЛЬКО JSON, без рассуждений и без вызовов инструментов!"
        )
        state.ai_messages.append({"role": "assistant", "content": f"{tool_name}({tool_args})"})
        state.ai_messages.append({"role": "user", "content": tool_msg})

        resp = await ai_service.step1_call_ai(state.ai_messages, use_tools=False)
        content = resp.choices[0].message.content or ""

        # Обработать возможные tool_calls от повторного запроса
        msg = resp.choices[0].message
        if msg.tool_calls:
            msg = await ai_service.resolve_tool_calls(state.ai_messages, msg)
            content = msg.content or ""

        query_dict = _extract_json(content)
        if query_dict and query_dict.get("entity"):
            return ODataQuery.from_dict(query_dict)

        state.ai_response_content = content
        return None


class AutoSearchResolver(ToolResolver):
    """Уровень 4: автоматический поиск сущности по ключевому слову.

    Последний fallback — извлечь существительное из запроса пользователя,
    найти сущность через metadata.search_entities() и повторить запрос.
    """

    _STOP_WORDS = {
        "покажи", "показать", "список", "все", "всех", "дай", "дайте",
        "найди", "найти", "выведи", "получить", "сколько", "какие",
        "какой", "какая", "где", "кто", "что", "это", "мне", "нам",
        "можно", "нужно", "хочу", "посмотри", "посмотреть", "и", "в",
        "на", "из", "за", "по", "с", "от", "до", "для", "не", "а",
        "но", "к", "о", "у", "те", "эти", "тот", "тотже", "этот",
    }

    def __init__(
        self,
        metadata: Any,
        next_resolver: ToolResolver | None = None,
    ) -> None:
        super().__init__(next_resolver)
        self._metadata = metadata

    def _guess_keyword(self, user_text: str) -> str | None:
        """Извлечь ключевое слово для поиска сущности."""
        words = re.findall(r'[а-яА-ЯёЁa-zA-Z0-9]{3,}', user_text)
        keywords = [w for w in words if w.lower() not in self._STOP_WORDS]
        return keywords[0] if keywords else None

    async def _try_resolve(
        self,
        state: ODataState,
        ai_service: Any,
    ) -> ODataQuery | None:
        from bot.agents.odata.state import ODataQuery

        keyword = self._guess_keyword(state.user_text)
        if not keyword:
            return None

        results = self._metadata.search_entities(keyword)
        if not results:
            return None

        log.info("Auto-search by keyword '%s': found %d entities", keyword, len(results))

        results_str = json.dumps(
            {"query": keyword, "results": results, "count": len(results)},
            ensure_ascii=False,
        )
        retry_msg = (
            f"[Автоматический поиск сущностей по запросу '{keyword}']: {results_str}\n\n"
            f"Используй найденную сущность и СРАЗУ построй OData-запрос JSON для: {state.user_text}\n"
            f"⚠️ ТОЛЬКО JSON, без рассуждений! Выбери наиболее подходящую сущность из результатов."
        )
        state.ai_messages.append({"role": "assistant", "content": f"search_entities(query='{keyword}')"})
        state.ai_messages.append({"role": "user", "content": retry_msg})

        try:
            resp = await ai_service.step1_call_ai(state.ai_messages, use_tools=False)
            content = resp.choices[0].message.content or ""
            query_dict = _extract_json(content)
            if query_dict and query_dict.get("entity"):
                return ODataQuery.from_dict(query_dict)
        except Exception as e:
            log.warning("Auto-search retry failed: %s", e)

        return None


# -- JSON extraction utility --

def _extract_json(text: str) -> dict | None:
    """Извлечь JSON-объект из текста ответа AI.

    Поддерживает:
    - JSON с мусором до/после
    - Markdown-обёртки (```json ... ```)
    - Префиксы ``tool_calls:``
    """
    text = text.strip()
    # Убрать повторяющиеся префиксы вида "tool_calls:" перед JSON
    text = re.sub(r'^(?:tool_calls:\s*)+', '', text)
    # Убрать markdown-обёртки
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
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
                    candidate = text[start: i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
