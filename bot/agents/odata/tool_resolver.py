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
from typing import TYPE_CHECKING, Any, cast

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
RETRY_JSON_HINT = _RETRY_JSON_HINT


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
    _TEXT_TOOL_POSitional_RE = re.compile(
        r"\b(search_entities|get_entity_fields|odata_reference)\s*\(\s*"
        r'[\'"]([^\'"]+)[\'"]\s*\)'
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
        if match:
            tool_name = match.group(1)
            param_name = match.group(2)
            param_value = match.group(3)
        else:
            pos = self._TEXT_TOOL_POSitional_RE.search(content)
            if not pos:
                return None
            tool_name = pos.group(1)
            param_name = "query" if tool_name == "search_entities" else "entity_name"
            if tool_name == "odata_reference":
                param_name = "topic"
            param_value = pos.group(2)

        if tool_name not in self._TOOL_NAMES:
            return None

        if tool_name == "search_entities" and param_value.lower() in _VISUAL_STOP_WORDS:
            log.info("Пропуск text tool call search_entities(%r) — визуальный стоп-слово", param_value)
            return None

        tool_args = {param_name: param_value}
        return await _execute_tool_and_retry_step1(state, ai_service, tool_name, tool_args)


class DsmlToolCallResolver(ToolResolver):
    """Уровень 2b: DeepSeek DSML/XML tool calls в content."""

    _DSML_INVOKE = re.compile(
        r'<\|?(?:DSML|tool_calls)\|?(?:invoke|function)\s+name=["\']'
        r'(search_entities|get_entity_fields|odata_reference)["\']',
        re.I,
    )
    _DSML_PARAM = re.compile(
        r'<\|?(?:DSML|tool_calls)\|?(?:parameter|arg)\s+name=["\'](\w+)["\'][^>]*>'
        r"([^<]+)</",
        re.I,
    )

    async def _try_resolve(
        self,
        state: ODataState,
        ai_service: Any,
    ) -> ODataQuery | None:
        content = state.ai_response_content
        if not content or "DSML" not in content and "tool_calls" not in content.lower():
            return None

        invokes = list(self._DSML_INVOKE.finditer(content))
        if not invokes:
            return None

        tool_name = invokes[0].group(1)
        params: dict[str, str] = {}
        for pm in self._DSML_PARAM.finditer(content):
            params[pm.group(1)] = pm.group(2).strip()

        if tool_name == "search_entities":
            param_value = params.get("query", "")
            if param_value.lower() in _VISUAL_STOP_WORDS:
                return None
            tool_args = {"query": param_value}
        elif tool_name == "get_entity_fields":
            tool_args = {"entity_name": params.get("entity_name", params.get("entity", ""))}
        else:
            tool_args = {"topic": params.get("topic", "")}

        if not any(tool_args.values()):
            log.warning("DSML tool call without parameters: %s", tool_name)
            return None

        log.info("Распознан DSML tool call: %s(%s)", tool_name, tool_args)
        return await _execute_tool_and_retry_step1(state, ai_service, tool_name, tool_args)


async def _execute_tool_and_retry_step1(
    state: ODataState,
    ai_service: Any,
    tool_name: str,
    tool_args: dict[str, str],
) -> ODataQuery | None:
    """Выполнить инструмент и повторить Step1 с результатом."""
    result = ai_service.handle_tool_call(tool_name, tool_args)
    log.info("Tool result (%s): %s", tool_name, result[:500] if result else "")

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

    # Массив объектов [{...}]
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                return cast(dict[Any, Any], parsed[0])
        except json.JSONDecodeError:
            pass

    start = 0
    while True:
        start = text.find("{", start)
        if start == -1:
            break
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        return cast(dict[Any, Any], json.loads(candidate))
                    except json.JSONDecodeError:
                        start = i + 1
                        break
        else:
            break
        if depth != 0:
            break

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return cast(dict[Any, Any], parsed)
    except json.JSONDecodeError:
        return None
    return None
