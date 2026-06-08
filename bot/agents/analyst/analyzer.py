#!/usr/bin/env python3
"""Сервис анализа метаданных: MCP tool loop + HTTP fallback."""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import AsyncOpenAI

from bot.agents.analyst.models import MetadataBrief, MetadataObject
from bot.agents.analyst.profile_loader import format_profile_block, load_profile
from bot.agents.analyst.prompts import ANALYST_SYSTEM, SUBMIT_METADATA_BRIEF_TOOL, meta_to_odata_entity
from bot.agents.odata.conf_doc_context import (
    build_conf_doc_search_queries,
    fetch_conf_doc_context,
)
from bot.config import AnalystSettings
from bot.mcp_client import MCPClientManager
from bot.utils import RateLimiter
from bot_lib.ai_retry import chat_completions_with_retry

log = logging.getLogger(__name__)

_DEFAULT_ALLOWED_TOOLS = [
    "conf_doc_search",
    "conf_doc_get_object",
    "conf_doc_get_object_chunk",
    "conf_doc_list_objects",
    "conf_doc_list_configurations",
    "conf_doc_health",
    "searxng_web_search",
    "web_url_read",
]

_CONF_DOC_DEPTH_TOOLS = frozenset(
    {
        "conf_doc_search",
        "conf_doc_get_object",
        "conf_doc_get_object_chunk",
        "conf_doc_query",
        "conf_doc_list_objects",
    }
)
_WEB_SEARCH_TOOLS = frozenset({"searxng_web_search", "web_url_read"})
_CONF_DOC_GATE_MSG = (
    "Сначала выполни conf_doc_search (keyword) и при необходимости "
    "conf_doc_get_object / conf_doc_get_object_chunk. "
    "SearXNG и submit_metadata_brief доступны только после conf-doc."
)


class AnalystService:
    """Анализ вопроса пользователя → MetadataBrief."""

    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        settings: AnalystSettings,
        mcp_manager: MCPClientManager | None,
        rate_limiter: RateLimiter,
        temperature: float = 0.1,
    ) -> None:
        self._client = client
        self._model = model
        self._settings = settings
        self._mcp = mcp_manager
        self._rate_limiter = rate_limiter
        self._temperature = temperature

    def _build_tools(self) -> list[dict]:
        tools: list[dict] = [SUBMIT_METADATA_BRIEF_TOOL]
        if self._mcp and self._mcp.is_connected():
            allowed = set(self._settings.allowed_mcp_tools or _DEFAULT_ALLOWED_TOOLS)
            for t in self._mcp.get_all_openai_tools():
                fname = t["function"]["name"]
                if fname in allowed:
                    tools.append(t)
        return tools

    def _conf_doc_required(self) -> bool:
        if not self._mcp or not self._mcp.is_connected():
            return False
        allowed = set(self._settings.allowed_mcp_tools or _DEFAULT_ALLOWED_TOOLS)
        return any(t in allowed for t in _CONF_DOC_DEPTH_TOOLS)

    async def analyze(
        self,
        user_text: str,
        *,
        chat_id: int | None = None,
        request_brief: str | None = None,
    ) -> MetadataBrief:
        """Проанализировать вопрос и вернуть MetadataBrief."""
        if self._mcp and self._mcp.is_connected():
            brief = await self._analyze_via_mcp(user_text, chat_id=chat_id)
            if brief.primary_objects or brief.intent:
                return brief
            log.info("Analyst MCP loop: empty result, trying fallback")

        return await self._analyze_via_fallback(user_text, request_brief=request_brief)

    async def _analyze_via_mcp(
        self,
        user_text: str,
        *,
        chat_id: int | None = None,
    ) -> MetadataBrief:
        profile_block = format_profile_block(self._settings.profile_path)
        system = ANALYST_SYSTEM.format(profile_block=profile_block)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ]
        tools = self._build_tools()
        conf_doc_explored = await self._preflight_conf_doc(user_text, messages)
        require_conf_doc = self._conf_doc_required()

        for iteration in range(self._settings.max_tool_iterations):
            await self._rate_limiter.wait()
            resp = await chat_completions_with_retry(
                self._client,
                step="analyst",
                model=self._model,
                retry_count=2,
                retry_delay=3,
                messages=messages,
                tools=tools if tools else None,
                tool_choice="auto" if tools else None,
                temperature=self._temperature,
            )
            choice = resp.choices[0]
            msg = choice.message

            if msg.tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in msg.tool_calls
                        ],
                    }
                )
                for tc in msg.tool_calls:
                    fname = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}

                    if fname == "submit_metadata_brief":
                        if require_conf_doc and not conf_doc_explored:
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc.id,
                                    "content": _CONF_DOC_GATE_MSG,
                                }
                            )
                            continue
                        brief = MetadataBrief.from_dict(args)
                        self._fill_odata_entities(brief)
                        log.info(
                            "Analyst MCP: brief ready (iter=%d, intent=%s, primary=%d)",
                            iteration + 1,
                            brief.intent,
                            len(brief.primary_objects),
                        )
                        return brief

                    if fname in _WEB_SEARCH_TOOLS and require_conf_doc and not conf_doc_explored:
                        result = _CONF_DOC_GATE_MSG
                    else:
                        result = await self._call_mcp_tool(fname, args)
                        if fname in _CONF_DOC_DEPTH_TOOLS:
                            conf_doc_explored = True
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        }
                    )
                continue

            # Текстовый ответ без tool calls — попробовать распарсить JSON
            content = (msg.content or "").strip()
            if content:
                parsed = self._try_parse_brief_json(content)
                if parsed:
                    return parsed

        return MetadataBrief()

    async def _preflight_conf_doc(
        self,
        user_text: str,
        messages: list[dict[str, Any]],
    ) -> bool:
        """Обязательный conf_doc_search до AI-loop (keyword из вопроса)."""
        if not self._conf_doc_required():
            return False

        queries = build_conf_doc_search_queries(user_text)[:2]
        if not queries:
            return False

        blocks: list[str] = []
        for query in queries:
            result = await self._call_mcp_tool("conf_doc_search", {"query": query, "top_k": 5})
            if result.startswith("Ошибка MCP") or result.startswith("Неизвестный инструмент"):
                log.warning("Analyst preflight conf_doc_search(%r) failed: %s", query, result[:200])
                continue
            blocks.append(f"### conf_doc_search({query!r})\n{result[:6000]}")

        if not blocks:
            return False

        messages.append(
            {
                "role": "user",
                "content": (
                    "Предварительные результаты conf-doc (preflight). Сверь с profile; "
                    "при необходимости — conf_doc_get_object / conf_doc_get_object_chunk. "
                    "SearXNG только если этого контекста недостаточно.\n\n" + "\n\n".join(blocks)
                ),
            }
        )
        log.info("Analyst preflight: %d conf_doc_search queries", len(blocks))
        return True

    async def _call_mcp_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if not self._mcp:
            return f"MCP недоступен для инструмента {name}"
        try:
            return await self._mcp.call_tool(name, arguments)
        except Exception as exc:
            log.warning("Analyst MCP tool %s failed: %s", name, exc)
            return f"Ошибка MCP [{name}]: {exc}"

    async def _analyze_via_fallback(
        self,
        user_text: str,
        *,
        request_brief: str | None = None,
    ) -> MetadataBrief:
        """HTTP conf-doc + эвристики из profile (без MCP)."""
        conf = self._settings.conf_doc_fallback
        if not conf.enabled:
            return self._brief_from_profile_only(user_text)

        block = await fetch_conf_doc_context(
            user_text,
            conf,
            request_brief=request_brief,
        )
        if not block:
            return self._brief_from_profile_only(user_text)

        queries = build_conf_doc_search_queries(user_text, request_brief)
        primary: list[MetadataObject] = []
        avoid: list[str] = []

        for line in block.splitlines():
            line = line.strip()
            if line.startswith("- ") and "." in line:
                # формат: - InformationRegister.АналитикаОстатковОтпусков (score=...)
                head = line[2:].split("(")[0].strip()
                if "." in head:
                    meta_type, name = head.split(".", 1)
                    primary.append(
                        MetadataObject(
                            meta_type=meta_type,
                            name=name,
                            odata_entity=meta_to_odata_entity(meta_type, name),
                            role="primary",
                            reason="conf-doc fallback",
                        )
                    )

        profile_text = load_profile(self._settings.profile_path)
        if "InformationRegister_ОстаткиОтпусков" in profile_text or "ОстаткиОтпусков" in profile_text:
            if "404" in profile_text or "нет в OData" in profile_text.lower():
                avoid.append("InformationRegister_ОстаткиОтпусков")

        intent = queries[0][:80] if queries else user_text[:80]
        return MetadataBrief(
            intent=intent,
            primary_objects=primary[:5],
            avoid=avoid,
            conf_doc_queries=queries,
            notes="Сформировано через HTTP fallback conf-doc",
        )

    def _brief_from_profile_only(self, user_text: str) -> MetadataBrief:
        return MetadataBrief(
            intent=user_text[:80],
            notes="MCP и conf-doc недоступны; используй profile и $metadata",
        )

    @staticmethod
    def _fill_odata_entities(brief: MetadataBrief) -> None:
        for obj in brief.primary_objects + brief.secondary_objects:
            if not obj.odata_entity and obj.meta_type and obj.name:
                obj.odata_entity = meta_to_odata_entity(obj.meta_type, obj.name)

    @staticmethod
    def _try_parse_brief_json(content: str) -> MetadataBrief | None:
        text = content.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if len(lines) > 2 else lines)
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                brief = MetadataBrief.from_dict(data)
                AnalystService._fill_odata_entities(brief)
                return brief
        except json.JSONDecodeError:
            pass
        return None
