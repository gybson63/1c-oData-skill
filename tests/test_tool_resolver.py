#!/usr/bin/env python3
"""Тесты tool_resolver.py."""

from __future__ import annotations

import pytest

from bot.agents.odata.state import ODataState
from bot.agents.odata.tool_resolver import (
    AutoSearchResolver,
    TextToolCallResolver,
    _try_apply_step1_content,
    _user_wants_chart,
)
from tests.test_query_parser import LOG_ANALYTICS_JSON


class _FakeMetadata:
    def search_entities(self, query: str) -> list[str]:
        if query.lower().startswith("сотруд"):
            return ["Catalog_Сотрудники"]
        return []


def test_guess_keyword_skips_visual_stop_words():
    resolver = AutoSearchResolver(metadata=_FakeMetadata())
    keyword = resolver._guess_keyword("Покажи график сотрудников по организациям")
    assert keyword == "сотрудников"


def test_user_wants_chart():
    assert _user_wants_chart("Покажи график сотрудников по организациям")
    assert not _user_wants_chart("Покажи список сотрудников")


@pytest.mark.asyncio
async def test_text_tool_call_skips_visual_search_query():
    resolver = TextToolCallResolver()

    class _FakeAI:
        async def step1_call_ai(self, *args, **kwargs):
            raise AssertionError("AI should not be called for visual stop word")

        def handle_tool_call(self, *args, **kwargs):
            raise AssertionError("Tool should not be called for visual stop word")

    state = ODataState(
        user_text="Покажи график сотрудников",
        ai_response_content="search_entities(query='график')",
    )
    result = await resolver._try_resolve(state, _FakeAI())
    assert result is None
    assert state.analytics_plan is None


def test_try_apply_step1_content_analytics():
    state = ODataState(user_text="Покажи график сотрудников по организациям")
    result = _try_apply_step1_content(state, LOG_ANALYTICS_JSON)
    assert result is None
    assert state.analytics_plan is not None
    assert state.analytics_plan.queries[0].entity == "Catalog_Сотрудники"
