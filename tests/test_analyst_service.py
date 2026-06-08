#!/usr/bin/env python3
"""Тесты gating conf-doc в AnalystService."""

from __future__ import annotations

from bot.agents.analyst.analyzer import _CONF_DOC_DEPTH_TOOLS, AnalystService
from bot.config import AnalystSettings


class _FakeMcp:
    def __init__(self, tool_names: list[str]) -> None:
        self._tools = tool_names

    def is_connected(self) -> bool:
        return True

    def get_all_openai_tools(self) -> list[dict]:
        return [{"function": {"name": n}} for n in self._tools]


def test_conf_doc_required_when_depth_tools_allowed():
    svc = AnalystService(
        client=None,  # type: ignore[arg-type]
        model="test",
        settings=AnalystSettings(),
        mcp_manager=_FakeMcp(list(_CONF_DOC_DEPTH_TOOLS)),  # type: ignore[arg-type]
        rate_limiter=None,  # type: ignore[arg-type]
    )
    assert svc._conf_doc_required() is True


def test_conf_doc_not_required_without_conf_doc_tools():
    svc = AnalystService(
        client=None,  # type: ignore[arg-type]
        model="test",
        settings=AnalystSettings(allowed_mcp_tools=["searxng_web_search"]),
        mcp_manager=_FakeMcp(["searxng_web_search"]),  # type: ignore[arg-type]
        rate_limiter=None,  # type: ignore[arg-type]
    )
    assert svc._conf_doc_required() is False
