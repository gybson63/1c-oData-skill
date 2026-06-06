#!/usr/bin/env python3
"""Тесты RequestBriefAdvisor."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.agents.odata.request_brief_advisor import (
    RequestBriefAdvisor,
    brief_from_rules,
    extract_current_query,
)
from bot.agents.odata.response_headline import apply_request_headline, strip_leading_headline


def test_extract_current_query_from_email_thread():
    text = "Контекст email-переписки (3 сообщений):\n\nОт: user\n\nСколько в отпуске?\n\n--- Текущий запрос ---\nКакой?"
    assert extract_current_query(text) == "Какой?"


def test_brief_from_rules_truncates_long_query():
    brief = brief_from_rules("Покажи всех сотрудников с должностью и подразделением в полном виде")
    assert "сотрудников" in brief.headline.lower()
    assert len(brief.headline) <= 90


def test_apply_request_headline_replaces_generic_title():
    brief = brief_from_rules("График численности по подразделениям")
    answer = apply_request_headline(
        "<b>📊 Аналитика</b>\n<i>пояснение</i>",
        brief,
    )
    assert answer.startswith("<b>")
    assert "Аналитика" not in answer.split("\n", 1)[0]
    assert "подраздел" in answer.lower()


def test_strip_leading_headline():
    assert strip_leading_headline("<b>📊 Старое</b>\nтело") == "тело"


@pytest.mark.asyncio
async def test_followup_uses_ai_when_available():
    advisor = RequestBriefAdvisor()
    ai = MagicMock()
    ai.request_brief = AsyncMock(return_value='{"headline": "Какой сотрудник в отпуске", "emoji": "👥"}')
    history = [
        {"role": "user", "content": "Сколько сотрудников в отпуске?"},
        {"role": "assistant", "content": "1"},
    ]
    brief = await advisor.advise(ai, user_query="Какой?", history=history)
    assert brief.source == "ai"
    assert "отпуск" in brief.headline.lower()
    ai.request_brief.assert_awaited_once()


@pytest.mark.asyncio
async def test_full_query_uses_rules_without_ai():
    advisor = RequestBriefAdvisor()
    query = "Покажи 10 штатных сотрудников с должностью"
    brief = await advisor.advise(None, user_query=query, history=[])
    assert brief.source == "rules"
    assert "сотрудник" in brief.headline.lower()
