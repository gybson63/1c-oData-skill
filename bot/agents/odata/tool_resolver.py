#!/usr/bin/env python3
"""Цепочка разрешения tool calls (Chain of Responsibility).

4 уровня fallback для получения OData-запроса от AI:

1. **NativeFunctionCallResolver** — стандартный OpenAI function calling
2. **InlineJsonResolver** — модель вернула tool call как JSON в content
3. **TextToolCallResolver** — модель вернула текстовый вызов (regex)
4. **AutoSearchResolver** — автоматический поиск сущности по ключевому слову

Каждый резолвер пытается обработать ответ AI и вернуть :class:`ODataQuery`
или заполнить :attr:`ODataState.analytics_plan`.
Если не удалось — передаёт следующему в цепочке.
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

_VISUAL_STOP_WORDS = frozenset(
    {
        "график",
        "графика",
        "графике",
        "графики",
        "графиков",
        "диаграмма",
        "диаграммы",
        "диаграмму",
        "chart",
        "diagram",
        "plot",
        "visualization",
        "визуализация",
        "визуализацию",
        "динамика",
        "динамику",
        "тренд",
        "тренда",
        "распределение",
        "распределения",
    }
)

_RETRY_JSON_HINT = (
    "⚠️ ТОЛЬКО JSON, без рассуждений и без вызовов инструментов!\n"
    "Для графика/сводки используй mode=analytics (queries, aggregate, chart)."
)


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
        if result is not None or state.analytics_plan:
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


def _try_apply_step1_content(state: ODataState, content: str) -> ODataQuery | None:
    """Распарсить JSON из content и применить к state (query или analytics)."""
    from bot.agents.odata.query_parser import apply_step1_dict

    query_dict = _extract_json(content)
    if not query_dict:
        return None
    if apply_step1_dict(state, query_dict):
        return state.query
    return None


def _user_wants_chart(user_text: str) -> bool:
    lower = user_text.lower()
    return any(word in lower for word in ("график", "диаграмм", "chart", "diagram", "визуал"))


class NativeFunctionCallResolver(ToolResolver):
    """Уровень 1: JSON query/analytics в content после function calling."""

    async def _try_resolve(
        self,
        state: ODataState,
        ai_service: Any,
    ) -> ODataQuery | None:
        content = state.ai_response_content
        if not content:
            return None
        return _try_apply_step1_content(state, content)


class InlineJsonResolver(ToolResolver):
    """Уровень 2: модель вернула tool call как JSON-объект в content."""

    _TOOL_NAMES = frozenset({"odata_reference", "get_entity_fields", "search_entities"})

    async def _try_resolve(
        self,
        state: ODataState,
        ai_service: Any,
    ) -> ODataQuery | None:
        content = state.ai_response_content
        if not content:
            return None

        parsed = _extract_json(content)
        if not parsed or not isinstance(parsed, dict):
            return None

        from bot.agents.odata.query_parser import is_inline_tool_call

        if "entity" in parsed or parsed.get("mode") == "analytics":
            return None

        if not is_inline_tool_call(parsed):
            return None

        tool_name = parsed.get("name") or parsed.get("function")
        log.warning("Обнаружен inline tool call: %s(%s)", tool_name, parsed["arguments"])

        result = ai_service.handle_tool_call(tool_name, parsed["arguments"])
        log.info("Inline tool result: %s", result[:300] if result else "")

        tool_msg = (
            f"[Результат инструмента {tool_name}]: {result}\n\n"
            f"Теперь построй JSON для: {state.user_text}\n{_RETRY_JSON_HINT}"
        )
        state.ai_messages.append({"role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)})
        state.ai_messages.append({"role": "user", "content": tool_msg})

        resp = await ai_service.step1_call_ai(state.ai_messages, use_tools=state.tools_supported, chat_id=state.chat_id)
        content = resp.choices[0].message.content or ""

        msg = resp.choices[0].message
        if msg.tool_calls:
            msg = await ai_service.resolve_tool_calls(state.ai_messages, msg, chat_id=state.chat_id)
            content = msg.content or ""

        state.ai_response_content = content
        return _try_apply_step1_content(state, content)


class TextToolCallResolver(ToolResolver):
    """Уровень 3: модель вернула текстовый вызов инструмента."""

    _TEXT_TOOL_RE = re.compile(
        r"\b(search_entities|get_entity_fields|odata_reference)\s*\(\s*"
        r'(\w+)\s*=\s*[\'"]([^\'"]*)[\'"]'
        r"\s*\)"
    )
    _TOOL_NAMES = frozenset({"odata_reference", "get_entity_fields", "search_entities"})

    async def _try_resolve(
        self,
        state: ODataState,
        ai_service: Any,
    ) -> ODataQuery | None:
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

        if tool_name == "search_entities" and param_value.lower() in _VISUAL_STOP_WORDS:
            log.info("Пропуск text tool call search_entities(%r) — визуальный стоп-слово", param_value)
            return None

        tool_args = {param_name: param_value}
        log.info("Распознан текстовый tool call: %s(%s)", tool_name, tool_args)

        result = ai_service.handle_tool_call(tool_name, tool_args)
        log.info("Text tool result: %s", result[:500] if result else "")

        tool_msg = (
            f"[Результат инструмента {tool_name}({tool_args})]: {result}\n\n"
            f"Теперь построй JSON для: {state.user_text}\n{_RETRY_JSON_HINT}"
        )
        state.ai_messages.append({"role": "assistant", "content": f"{tool_name}({tool_args})"})
        state.ai_messages.append({"role": "user", "content": tool_msg})

        resp = await ai_service.step1_call_ai(state.ai_messages, use_tools=False, chat_id=state.chat_id)
        content = resp.choices[0].message.content or ""

        msg = resp.choices[0].message
        if msg.tool_calls:
            msg = await ai_service.resolve_tool_calls(state.ai_messages, msg, chat_id=state.chat_id)
            content = msg.content or ""

        state.ai_response_content = content
        return _try_apply_step1_content(state, content)


class AutoSearchResolver(ToolResolver):
    """Уровень 4: автоматический поиск сущности по ключевому слову."""

    _STOP_WORDS = {
        "покажи",
        "показать",
        "список",
        "все",
        "всех",
        "дай",
        "дайте",
        "найди",
        "найти",
        "выведи",
        "получить",
        "сколько",
        "какие",
        "какой",
        "какая",
        "где",
        "кто",
        "что",
        "это",
        "мне",
        "нам",
        "можно",
        "нужно",
        "хочу",
        "посмотри",
        "посмотреть",
        "и",
        "в",
        "на",
        "из",
        "за",
        "по",
        "с",
        "от",
        "до",
        "для",
        "не",
        "а",
        "но",
        "к",
        "о",
        "у",
        "те",
        "эти",
        "тот",
        "тотже",
        "этот",
    } | _VISUAL_STOP_WORDS

    def __init__(
        self,
        metadata: Any,
        next_resolver: ToolResolver | None = None,
    ) -> None:
        super().__init__(next_resolver)
        self._metadata = metadata

    def _guess_keyword(self, user_text: str) -> str | None:
        """Извлечь ключевое слово для поиска сущности."""
        words = re.findall(r"[а-яА-ЯёЁa-zA-Z0-9]{3,}", user_text)
        keywords = [w for w in words if w.lower() not in self._STOP_WORDS]
        return keywords[0] if keywords else None

    async def _try_resolve(
        self,
        state: ODataState,
        ai_service: Any,
    ) -> ODataQuery | None:
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
        analytics_hint = ""
        if _user_wants_chart(state.user_text):
            analytics_hint = (
                "\nЗапрос про график/сводку — верни JSON с mode=analytics "
                "(queries, aggregate, chart), а не mode=query.\n"
            )
        retry_msg = (
            f"[Автоматический поиск сущностей по запросу '{keyword}']: {results_str}\n\n"
            f"Используй найденную сущность и СРАЗУ построй JSON для: {state.user_text}\n"
            f"{analytics_hint}{_RETRY_JSON_HINT}"
        )
        state.ai_messages.append({"role": "assistant", "content": f"search_entities(query='{keyword}')"})
        state.ai_messages.append({"role": "user", "content": retry_msg})

        try:
            resp = await ai_service.step1_call_ai(state.ai_messages, use_tools=False, chat_id=state.chat_id)
            content = resp.choices[0].message.content or ""
            state.ai_response_content = content
            return _try_apply_step1_content(state, content)
        except Exception as e:
            log.warning("Auto-search retry failed: %s", e)

        return None


def _extract_json(text: str) -> dict | None:
    """Извлечь JSON-объект из текста ответа AI."""
    text = text.strip()
    text = re.sub(r"^(?:tool_calls:\s*)+", "", text)
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()

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
