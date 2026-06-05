#!/usr/bin/env python3
"""Тесты query_parser.py."""

from __future__ import annotations

from bot.agents.odata.analytics_models import AnalyticsPlan
from bot.agents.odata.query_parser import apply_step1_dict, is_inline_tool_call
from bot.agents.odata.state import ODataState
from bot.agents.odata.tool_resolver import _extract_json

LOG_ANALYTICS_JSON = """```json
{
  "mode": "analytics",
  "queries": [
    {
      "alias": "employees",
      "entity": "Catalog_Сотрудники",
      "filter": "DeletionMark eq false",
      "select": [
        "ГоловнаяОрганизация_Key"
      ],
      "top": 200
    }
  ],
  "joins": [],
  "aggregate": {
    "group_by": [
      "ГоловнаяОрганизация_Key"
    ],
    "agg": {
      "КоличествоСотрудников": "count"
    }
  },
  "chart": {
    "type": "bar",
    "x": "ГоловнаяОрганизация_Key",
    "y": "КоличествоСотрудников",
    "title": "Распределение сотрудников по организациям"
  },
  "explanation": "Для построения графика распределения сотрудников по организациям используется справочник 'Сотрудники' с группировкой по полю 'ГоловнаяОрганизация'."
}
```"""


def test_extract_json_from_markdown_analytics():
    parsed = _extract_json(LOG_ANALYTICS_JSON)
    assert parsed is not None
    assert parsed["mode"] == "analytics"
    assert parsed["queries"][0]["entity"] == "Catalog_Сотрудники"


def test_apply_step1_analytics_plan():
    state = ODataState(user_text="Покажи график сотрудников по организациям")
    parsed = _extract_json(LOG_ANALYTICS_JSON)
    assert apply_step1_dict(state, parsed)
    assert isinstance(state.analytics_plan, AnalyticsPlan)
    assert state.analytics_plan.queries[0].entity == "Catalog_Сотрудники"
    assert state.analytics_plan.chart is not None
    assert state.analytics_plan.chart.y == "КоличествоСотрудников"


def test_apply_step1_entity_query():
    state = ODataState(user_text="Покажи организации")
    ok = apply_step1_dict(
        state,
        {
            "entity": "Catalog_Организации",
            "filter": "DeletionMark eq false",
            "select": "Description",
            "top": 20,
        },
    )
    assert ok
    assert state.query is not None
    assert state.query.entity == "Catalog_Организации"
    assert state.analytics_plan is None


def test_is_inline_tool_call_rejects_analytics():
    assert not is_inline_tool_call({"mode": "analytics", "queries": []})


def test_is_inline_tool_call_accepts_search_entities():
    assert is_inline_tool_call(
        {"name": "search_entities", "arguments": {"query": "Сотрудники"}},
    )
