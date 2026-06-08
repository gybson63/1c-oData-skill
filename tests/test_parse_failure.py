#!/usr/bin/env python3
"""Тесты parse_failure и журнала ошибок разбора Step 1."""

from __future__ import annotations

from bot.agents.odata.parse_failure import (
    ParseFailureReason,
    classify_step1_failure,
    journal_parse_failure,
    load_parse_failures,
    response_is_parse_failure,
    setup_parse_failure_journal,
)
from bot.agents.odata.query_parser import apply_step1_dict, get_last_apply_failure
from bot.agents.odata.state import ODataState
from bot.metrics import metrics, reset_metrics


def test_response_is_parse_failure():
    assert response_is_parse_failure("⚠️ Не удалось разобрать запрос. Попробуйте переформулировать.")
    assert not response_is_parse_failure("Список из 10 сотрудников")


def test_classify_json_not_found():
    reason = classify_step1_failure("", query_dict=None)
    assert reason == ParseFailureReason.JSON_NOT_FOUND


def test_classify_inline_tool_shape():
    reason = classify_step1_failure(
        '{"name":"search_entities","arguments":{"query":"x"}}',
        query_dict={"name": "search_entities", "arguments": {"query": "x"}},
    )
    assert reason == ParseFailureReason.INLINE_TOOL_SHAPE


def test_classify_empty_entity():
    reason = classify_step1_failure('{"filter":"x"}', query_dict={"filter": "x"})
    assert reason == ParseFailureReason.EMPTY_ENTITY


def test_apply_step1_analytics_invalid():
    state = ODataState(user_text="test")
    ok = apply_step1_dict(state, {"mode": "analytics", "queries": []})
    assert ok is False
    assert get_last_apply_failure() == ParseFailureReason.ANALYTICS_INVALID


def test_journal_parse_failure(tmp_path):
    setup_parse_failure_journal(str(tmp_path))
    reset_metrics()
    journal_parse_failure(
        user_query="Покажи 5 сотрудников",
        ai_response='{"broken":',
        reason=ParseFailureReason.JSON_NOT_FOUND,
        channel="email",
        chat_id=-1,
    )
    records = load_parse_failures(str(tmp_path))
    assert len(records) == 1
    assert records[0]["failure_reason"] == "json_not_found"
    assert records[0]["user_query"] == "Покажи 5 сотрудников"
    assert metrics.get_counter("odata_step1_parse_failures") == 1
