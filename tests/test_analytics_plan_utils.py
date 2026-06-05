#!/usr/bin/env python3
"""Тесты analytics_plan_utils и analytics data pipeline."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.agents.odata.analytics_executor import AnalyticsExecutor
from bot.agents.odata.analytics_models import AnalyticsPlan
from bot.agents.odata.analytics_plan_utils import align_dataframe_to_plan, normalize_analytics_plan
from bot.agents.odata.response_parser import resolve_references, resolve_references_for_analytics
from bot_lib.dataframe import aggregate_dataframe, records_to_dataframe
from tests.test_query_parser import LOG_ANALYTICS_JSON


def test_normalize_plan_from_log_strips_alias_and_adds_expand():
    from bot.agents.odata.analytics_models import AnalyticsPlan
    from bot.agents.odata.tool_resolver import _extract_json

    raw = _extract_json(LOG_ANALYTICS_JSON)
    assert raw is not None
    plan = normalize_analytics_plan(AnalyticsPlan.from_dict(raw))

    assert plan.aggregate is not None
    assert plan.aggregate.group_by == ["ГоловнаяОрганизация"]
    assert plan.chart is not None
    assert plan.chart.x == "ГоловнаяОрганизация"
    assert plan.queries[0].expand == "ГоловнаяОрганизация"


def test_normalize_plan_odata_path_description():
    plan = normalize_analytics_plan(
        AnalyticsPlan.from_dict(
            {
                "mode": "analytics",
                "queries": [
                    {
                        "alias": "employees",
                        "entity": "Catalog_Сотрудники",
                        "filter": "DeletionMark eq false",
                        "select": "ГоловнаяОрганизация_Key",
                        "top": 200,
                    }
                ],
                "aggregate": {
                    "group_by": ["ГоловнаяОрганизация/Description"],
                    "agg": {"КоличествоСотрудников": "count"},
                },
                "chart": {
                    "type": "bar",
                    "x": "ГоловнаяОрганизация/Description",
                    "y": "КоличествоСотрудников",
                },
            }
        )
    )

    assert plan.aggregate.group_by == ["ГоловнаяОрганизация"]
    assert plan.chart.x == "ГоловнаяОрганизация"
    assert plan.queries[0].expand == "ГоловнаяОрганизация"


def test_align_dataframe_renames_key_column():
    import pandas as pd

    from bot.agents.odata.analytics_models import AggregateSpec, AnalyticsSubQuery, ChartSpec

    plan = AnalyticsPlan(
        queries=[AnalyticsSubQuery(alias="q", entity="Catalog_Сотрудники")],
        aggregate=AggregateSpec(
            group_by=["ГоловнаяОрганизация"],
            agg={"Количество": "count"},
        ),
        chart=ChartSpec(type="bar", x="ГоловнаяОрганизация", y="Количество"),
    )
    df = pd.DataFrame({"ГоловнаяОрганизация_Key": ["a", "b", "a"]})
    aligned = align_dataframe_to_plan(df, plan)
    assert "ГоловнаяОрганизация" in aligned.columns


def test_normalize_plan_from_user_log_with_alias_prefix():
    plan = normalize_analytics_plan(
        AnalyticsPlan.from_dict(
            {
                "mode": "analytics",
                "queries": [
                    {
                        "alias": "employees",
                        "entity": "Catalog_Сотрудники",
                        "filter": "DeletionMark eq false",
                        "select": "ГоловнаяОрганизация_Key",
                        "top": 200,
                    }
                ],
                "aggregate": {
                    "group_by": ["employees/ГоловнаяОрганизация_Key"],
                    "agg": {"Сотрудники": "count"},
                },
                "chart": {
                    "type": "bar",
                    "x": "employees/ГоловнаяОрганизация_Key",
                    "y": "Сотрудники",
                    "title": "Распределение сотрудников по организациям",
                },
            }
        )
    )

    assert plan.aggregate.group_by == ["ГоловнаяОрганизация"]
    assert plan.chart.x == "ГоловнаяОрганизация"
    assert plan.queries[0].expand == "ГоловнаяОрганизация"


def test_resolve_references_for_analytics_keeps_key_without_expand():
    records = [
        {"ГоловнаяОрганизация_Key": "guid-a"},
        {"ГоловнаяОрганизация_Key": "guid-a"},
        {"ГоловнаяОрганизация_Key": "guid-b"},
    ]

    plain = resolve_references(records)
    assert plain == [{}, {}, {}]

    analytics = resolve_references_for_analytics(records)
    df = records_to_dataframe(analytics)
    assert len(df) == 3
    assert "ГоловнаяОрганизация_Key" in df.columns

    grouped = aggregate_dataframe(
        df,
        ["ГоловнаяОрганизация_Key"],
        {"Сотрудники": "count"},
    )
    assert len(grouped) == 2
    assert grouped["Сотрудники"].sum() == 3


def test_resolve_references_for_analytics_uses_expand_labels():
    records = [
        {
            "ГоловнаяОрганизация_Key": "guid-a",
            "ГоловнаяОрганизация": {"Description": "ООО Альфа", "Ref_Key": "guid-a"},
        },
        {
            "ГоловнаяОрганизация_Key": "guid-b",
            "ГоловнаяОрганизация": {"Description": "ООО Бета", "Ref_Key": "guid-b"},
        },
    ]
    resolved = resolve_references_for_analytics(records)
    df = records_to_dataframe(resolved)
    assert "ГоловнаяОрганизация" in df.columns
    grouped = aggregate_dataframe(df, ["ГоловнаяОрганизация"], {"Сотрудники": "count"})
    assert set(grouped["ГоловнаяОрганизация"]) == {"ООО Альфа", "ООО Бета"}


@pytest.mark.asyncio
async def test_analytics_executor_normalizes_plan_before_fetch():
    executor = MagicMock()
    executor.execute = AsyncMock(
        return_value=(
            [
                {
                    "ГоловнаяОрганизация_Key": "guid-a",
                    "ГоловнаяОрганизация": {"Description": "ООО Альфа"},
                },
                {
                    "ГоловнаяОрганизация_Key": "guid-a",
                    "ГоловнаяОрганизация": {"Description": "ООО Альфа"},
                },
            ],
            2,
        )
    )

    plan = AnalyticsPlan.from_dict(
        {
            "mode": "analytics",
            "queries": [
                {
                    "alias": "employees",
                    "entity": "Catalog_Сотрудники",
                    "select": "ГоловнаяОрганизация_Key",
                    "top": 200,
                }
            ],
            "aggregate": {
                "group_by": ["employees/ГоловнаяОрганизация_Key"],
                "agg": {"Сотрудники": "count"},
            },
            "chart": {
                "type": "bar",
                "x": "employees/ГоловнаяОрганизация_Key",
                "y": "Сотрудники",
            },
        }
    )

    analytics = AnalyticsExecutor(executor, max_records=200, max_joins=3)
    result = await analytics.execute(plan)

    assert executor.execute.call_args.kwargs["expand"] == "ГоловнаяОрганизация"
    assert len(result.dataframe) == 1
    assert result.dataframe.iloc[0]["Сотрудники"] == 2
