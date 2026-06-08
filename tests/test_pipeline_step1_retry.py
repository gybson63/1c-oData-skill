#!/usr/bin/env python3
"""Тест финального retry Step 1 в pipeline."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.agents.odata.pipeline import ODataPipeline
from bot.agents.odata.state import ODataState


class _FakeMessage:
    def __init__(self, content: str, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]


@pytest.mark.asyncio
async def test_step1_final_retry_succeeds_after_invalid_first_response():
    ai = MagicMock()
    ai.step1_call_ai = AsyncMock(
        side_effect=[
            _FakeResponse("Это не JSON, просто текст."),
            _FakeResponse(
                '{"entity":"Catalog_Сотрудники","filter":null,'
                '"select":"Description","orderby":null,"top":5,"count":false}'
            ),
        ]
    )
    ai.resolve_tool_calls = AsyncMock()

    pipeline = ODataPipeline(
        ai=ai,
        executor=MagicMock(),
        validator=MagicMock(),
        metadata=MagicMock(),
        rate_limiter=None,
        tools=[],
        model="test-model",
    )
    pipeline._tool_chain = MagicMock()
    pipeline._tool_chain.resolve = AsyncMock(return_value=None)

    state = ODataState(user_text="Покажи 5 сотрудников", chat_id=1)
    await pipeline._step_build_query(state)

    assert state.query is not None
    assert state.query.entity == "Catalog_Сотрудники"
    assert ai.step1_call_ai.await_count == 2
