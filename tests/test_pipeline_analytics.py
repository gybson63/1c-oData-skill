#!/usr/bin/env python3
"""Тесты analytics-ветки pipeline."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from bot.agents.odata.analytics_executor import AnalyticsResult
from bot.agents.odata.analytics_models import AnalyticsPlan, AnalyticsSubQuery, ChartSpec
from bot.agents.odata.pipeline import ODataPipeline
from bot.agents.odata.request_brief_advisor import brief_from_rules
from bot.agents.odata.state import ODataState


@pytest.mark.asyncio
async def test_step_analytics_with_chart():
    ai = MagicMock()
    executor = MagicMock()
    validator = MagicMock()
    metadata = MagicMock()

    pipeline = ODataPipeline(
        ai=ai,
        executor=executor,
        validator=validator,
        metadata=metadata,
        rate_limiter=None,
        tools=[],
        model="test",
        chart_max_categories=10,
    )

    plan = AnalyticsPlan(
        queries=[AnalyticsSubQuery(alias="q", entity="Catalog_Товары", top=10)],
        chart=ChartSpec(type="bar", x="Description", y="Amount", title="Sales"),
        explanation="Тестовый график",
    )

    df = pd.DataFrame({"Description": ["A", "B"], "Amount": [10, 20]})
    mock_analytics = MagicMock()
    mock_analytics.execute = AsyncMock(return_value=AnalyticsResult(dataframe=df, row_counts={"q": 2}, plan=plan))

    import bot.agents.odata.pipeline as pipeline_mod

    original = pipeline_mod.AnalyticsExecutor
    pipeline_mod.AnalyticsExecutor = MagicMock(return_value=mock_analytics)

    state = ODataState(user_text="график", analytics_plan=plan)
    state.request_brief = brief_from_rules("график")
    try:
        result = pipeline._finalize_answer(await pipeline._step_analytics(state))
    finally:
        pipeline_mod.AnalyticsExecutor = original

    assert "график" in result.answer_html.lower()
    assert "📊" in result.answer_html
    assert len(result.attachments) == 1
    assert result.attachments[0].content_type == "image/png"
    assert result.chart_html


@pytest.mark.asyncio
async def test_step_analytics_uses_normalized_plan_for_chart():
    from bot.agents.odata.analytics_plan_utils import normalize_analytics_plan

    ai = MagicMock()
    executor = MagicMock()
    validator = MagicMock()
    metadata = MagicMock()

    pipeline = ODataPipeline(
        ai=ai,
        executor=executor,
        validator=validator,
        metadata=metadata,
        rate_limiter=None,
        tools=[],
        model="test",
        chart_max_categories=10,
    )

    raw_plan = AnalyticsPlan.from_dict(
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
                "agg": {"КоличествоСотрудников": "count"},
            },
            "chart": {
                "type": "bar",
                "x": "ГоловнаяОрганизация_Key",
                "y": "КоличествоСотрудников",
                "title": "Распределение",
            },
        }
    )
    normalized = normalize_analytics_plan(raw_plan.model_copy(deep=True))
    df = pd.DataFrame(
        {
            "ГоловнаяОрганизация": ["ООО Альфа", "ООО Бета"],
            "КоличествоСотрудников": [10, 20],
        }
    )

    mock_analytics = MagicMock()
    mock_analytics.execute = AsyncMock(
        return_value=AnalyticsResult(
            dataframe=df,
            row_counts={"employees": 30},
            plan=normalized,
        )
    )

    import bot.agents.odata.pipeline as pipeline_mod

    original = pipeline_mod.AnalyticsExecutor
    pipeline_mod.AnalyticsExecutor = MagicMock(return_value=mock_analytics)

    state = ODataState(user_text="график по организациям", analytics_plan=raw_plan)
    state.request_brief = brief_from_rules("график по организациям")
    try:
        result = pipeline._finalize_answer(await pipeline._step_analytics(state))
    finally:
        pipeline_mod.AnalyticsExecutor = original

    assert "организац" in result.answer_html.lower()
    assert state.analytics_plan.chart is not None
    assert state.analytics_plan.chart.x == "ГоловнаяОрганизация"
    assert len(result.attachments) == 1
    assert result.chart_html
