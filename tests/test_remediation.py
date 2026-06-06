#!/usr/bin/env python3
"""Тесты field_aliases, config_hint, attachment_csv, error_handler refinements."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.agents.odata.attachment_csv import (
    extract_csv_tables,
    is_csv_analysis_request,
    try_handle_csv_attachment,
)
from bot.agents.odata.config_hint_loader import format_config_hint_block, load_config_hint
from bot.agents.odata.error_handler import parse_odata_error_message
from bot.agents.odata.field_aliases import (
    normalize_field_name,
    normalize_filter_expr,
    normalize_nav_select,
    normalize_query_dict,
)
from bot.agents.odata.query_parser import apply_step1_dict
from bot.agents.odata.state import ODataState
from bot.agents.odata.tool_resolver import DsmlToolCallResolver, TextToolCallResolver
from bot_lib.exceptions import ODataError


def test_normalize_field_alias():
    available = {"ПодразделениеОрганизации_Key", "Description"}
    assert normalize_field_name("Подразделение_Key", available) == "ПодразделениеОрганизации_Key"


def test_normalize_nav_select_to_key_fields():
    assert normalize_nav_select("Сотрудник/Description,Подразделение/Description") == (
        "Сотрудник_Key,ПодразделениеОрганизации_Key"
    )


def test_reverse_field_alias_for_record_type():
    available = {"Сотрудник_Key", "Должность_Key", "Подразделение_Key"}
    assert normalize_field_name("ПодразделениеОрганизации_Key", available) == "Подразделение_Key"


def test_normalize_filter_expr():
    expr = "Подразделение/Description eq 'IT' and Подразделение_Key ne null"
    out = normalize_filter_expr(expr)
    assert "ПодразделениеОрганизации" in out
    assert "Подразделение/" not in out


def test_normalize_query_dict_analytics_group_by():
    data = {
        "mode": "analytics",
        "queries": [{"alias": "q", "entity": "InformationRegister_X/SliceLast()"}],
        "aggregate": {"group_by": ["Подразделение_Key"], "agg": {"cnt": "count"}},
    }
    out = normalize_query_dict(data)
    assert out["aggregate"]["group_by"] == ["ПодразделениеОрганизации_Key"]


def test_apply_step1_normalizes_department_field():
    state = ODataState(user_text="test")
    ok = apply_step1_dict(
        state,
        {
            "entity": "InformationRegister_КадроваяИсторияСотрудников/SliceLast()",
            "select": "Сотрудник_Key,Подразделение_Key,Должность_Key",
            "top": 5,
        },
    )
    assert ok
    assert state.query
    assert "ПодразделениеОрганизации_Key" in (state.query.select or "")


def test_load_config_hint():
    hint = load_config_hint(Path("bot/config_hint.md"))
    assert "ПодразделениеОрганизации" in hint


def test_format_config_hint_block():
    block = format_config_hint_block(Path("bot/config_hint.md"))
    assert "СПРАВКА ПО КОНФИГУРАЦИИ" in block


def test_parse_odata_error_code6_department():
    err = ODataError(
        'HTTP 400 {"odata.error":{"code":"6","message":{"value":"Сегмент пути Подразделение не найден!"}}}',
        status_code=400,
    )
    msg = parse_odata_error_message(err)
    assert "ПодразделениеОрганизации" in msg


def test_parse_odata_error_code6_extra_segments():
    err = ODataError(
        'HTTP 400 {"odata.error":{"code":"6","message":{"value":"Обнаружены лишние сегменты!"}}}',
        status_code=400,
    )
    msg = parse_odata_error_message(err)
    assert "SliceLast" in msg or "Nav/Description" in msg


def test_is_csv_analysis_request():
    text = "Проанализируй данные\n\n--- Вложения ---\n--- data.csv ---\na,b\n1,2"
    assert is_csv_analysis_request(text)


def test_extract_csv_tables():
    text = "Анализ\n\n--- Вложения ---\n--- chislennost.csv ---\npodrazdelenie,sotrudniki\nОтдел кадров,12\n"
    tables = extract_csv_tables(text)
    assert len(tables) == 1
    assert tables[0][1][0]["podrazdelenie"] == "Отдел кадров"


@pytest.mark.asyncio
async def test_try_handle_csv_attachment():
    ai = MagicMock()
    ai.step2_format_response = AsyncMock(return_value="<b>Итого: 17</b>")

    state = ODataState(
        user_text=("Проанализируй приложенный список\n\n--- Вложения ---\n--- t.csv ---\na,b\n1,2\n"),
    )
    result = await try_handle_csv_attachment(state, ai)
    assert result is not None
    assert "t.csv" in result.answer_html
    ai.step2_format_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_text_tool_positional_query():
    resolver = TextToolCallResolver()

    class _FakeAI:
        def handle_tool_call(self, name, args):
            return json.dumps({"results": ["Catalog_X"]})

        async def step1_call_ai(self, messages, use_tools=False, chat_id=None):
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.content = '{"entity":"Catalog_X","top":5}'
            resp.choices[0].message.tool_calls = None
            return resp

    state = ODataState(
        user_text="test",
        ai_response_content="search_entities(query='Сотрудники')",
    )
    result = await resolver._try_resolve(state, _FakeAI())
    assert result is not None
    assert result.entity == "Catalog_X"


@pytest.mark.asyncio
async def test_dsml_tool_call_resolver():
    resolver = DsmlToolCallResolver()
    dsml = (
        "<|DSML|tool_calls>\n"
        '<|DSML|invoke name="search_entities">\n'
        '<|DSML|parameter name="query" string="true">Сотрудники</|DSML|parameter>\n'
        "</|DSML|invoke>\n"
        "</|DSML|tool_calls>"
    )

    class _FakeAI:
        def handle_tool_call(self, name, args):
            return json.dumps({"results": ["Catalog_Сотрудники"]})

        async def step1_call_ai(self, messages, use_tools=False, chat_id=None):
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.content = '{"entity":"Catalog_Сотрудники","select":"Description","top":10}'
            resp.choices[0].message.tool_calls = None
            return resp

    state = ODataState(user_text="Покажи сотрудников", ai_response_content=dsml)
    result = await resolver._try_resolve(state, _FakeAI())
    assert result is not None
    assert result.entity == "Catalog_Сотрудники"


def test_finalize_history_pagination_summary():
    state = ODataState(user_text="q")
    state.pagination_ctx = {"entity": "Catalog_Сотрудники", "filter": "DeletionMark eq false", "top": 10}
    hist = state.finalize_history(10)
    assistant = hist[-1]["content"]
    parsed = json.loads(assistant)
    assert parsed["_history_type"] == "pagination_ctx"
    assert "Catalog_Сотрудники" in parsed["_summary"]
