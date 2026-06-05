#!/usr/bin/env python3
"""Тесты субагента VisualizationAdvisor."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from bot.agents.odata.analytics_models import (
    AggregateSpec,
    AnalyticsPlan,
    AnalyticsSubQuery,
    ChartSpec,
)
from bot.agents.odata.visualization_advisor import VisualizationAdvisor


def _base_plan(**kwargs) -> AnalyticsPlan:
    defaults = {
        "queries": [AnalyticsSubQuery(alias="q", entity="Catalog_Test", top=50)],
        "explanation": "Тест",
    }
    defaults.update(kwargs)
    return AnalyticsPlan(**defaults)


@pytest.mark.asyncio
async def test_table_only_hint_skips_chart():
    advisor = VisualizationAdvisor()
    df = pd.DataFrame({"A": [1, 2], "B": [3, 4]})
    plan = _base_plan(chart=ChartSpec(type="bar", x="A", y="B", title="T"))

    decision = await advisor.advise(
        None,
        user_query="Покажи списком все записи",
        df=df,
        plan=plan,
    )

    assert decision.show_chart is False
    assert decision.show_table is True
    assert decision.source == "rules"


@pytest.mark.asyncio
async def test_plan_chart_with_valid_data_shows_chart():
    advisor = VisualizationAdvisor(max_categories=10)
    df = pd.DataFrame({"Dept": ["A", "B", "C"], "Cnt": [10, 20, 5]})
    plan = _base_plan(
        chart=ChartSpec(type="bar", x="Dept", y="Cnt", title="По отделам"),
        aggregate=AggregateSpec(group_by=["Dept"], agg={"Cnt": "sum"}),
    )

    decision = await advisor.advise(
        None,
        user_query="численность по подразделениям",
        df=df,
        plan=plan,
    )

    assert decision.show_chart is True
    assert decision.chart is not None
    assert decision.chart.x == "Dept"


@pytest.mark.asyncio
async def test_small_aggregate_without_chart_hint_prefers_table():
    advisor = VisualizationAdvisor()
    df = pd.DataFrame({"Org": ["A", "B"], "Cnt": [1, 2]})
    plan = _base_plan(
        aggregate=AggregateSpec(group_by=["Org"], agg={"Cnt": "count"}),
    )

    decision = await advisor.advise(
        None,
        user_query="сколько людей в каждой организации",
        df=df,
        plan=plan,
    )

    assert decision.show_chart is False
    assert decision.show_table is True


@pytest.mark.asyncio
async def test_chart_hint_proposes_from_aggregate():
    advisor = VisualizationAdvisor(pie_max_categories=10, max_categories=30)
    df = pd.DataFrame({"Org": ["A", "B", "C"], "Cnt": [5, 10, 3]})
    plan = _base_plan(
        aggregate=AggregateSpec(group_by=["Org"], agg={"Cnt": "count"}),
    )

    decision = await advisor.advise(
        None,
        user_query="построй диаграмму по организациям",
        df=df,
        plan=plan,
    )

    assert decision.show_chart is True
    assert decision.chart is not None
    assert decision.chart.x == "Org"


@pytest.mark.asyncio
async def test_ambiguous_query_uses_ai():
    advisor = VisualizationAdvisor(max_categories=30)
    df = pd.DataFrame({"Dept": ["A", "B", "C", "D"], "Cnt": [1, 2, 3, 4]})
    plan = _base_plan(
        aggregate=AggregateSpec(group_by=["Dept"], agg={"Cnt": "sum"}),
    )

    ai = MagicMock()
    ai.visualization_advise = AsyncMock(
        return_value=(
            '{"show_chart": true, "show_table": true, '
            '"chart": {"type": "bar", "x": "Dept", "y": "Cnt", "title": "T"}, '
            '"reason": "Сравнение категорий"}'
        )
    )

    decision = await advisor.advise(
        ai,
        user_query="сколько по отделам",
        df=df,
        plan=plan,
    )

    assert decision.show_chart is True
    assert decision.source == "ai"
    ai.visualization_advise.assert_awaited_once()


@pytest.mark.asyncio
async def test_ai_failure_falls_back_to_rules():
    advisor = VisualizationAdvisor(max_categories=30)
    df = pd.DataFrame({"Dept": ["A", "B", "C"], "Cnt": [1, 2, 3]})
    plan = _base_plan(
        chart=ChartSpec(type="bar", x="Dept", y="Cnt", title="T"),
        aggregate=AggregateSpec(group_by=["Dept"], agg={"Cnt": "sum"}),
    )

    ai = MagicMock()
    ai.visualization_advise = AsyncMock(side_effect=RuntimeError("AI down"))

    decision = await advisor.advise(
        ai,
        user_query="график по отделам",
        df=df,
        plan=plan,
    )

    assert decision.show_chart is True
    assert decision.source == "rules"
