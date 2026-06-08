#!/usr/bin/env python3
"""Тесты chart_type_optimizer."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from bot.agents.odata.analytics_executor import AnalyticsExecutor
from bot.agents.odata.analytics_models import AggregateSpec, AnalyticsPlan, AnalyticsSubQuery, ChartSpec
from bot.agents.odata.chart_type_optimizer import optimize_chart_type


def _plan_with_chart(chart_type: str = "bar") -> AnalyticsPlan:
    return AnalyticsPlan(
        queries=[AnalyticsSubQuery(alias="employees", entity="Catalog_Сотрудники", top=200)],
        aggregate=AggregateSpec(
            group_by=["ГоловнаяОрганизация"],
            agg={"КоличествоСотрудников": "count"},
        ),
        chart=ChartSpec(
            type=chart_type,
            x="ГоловнаяОрганизация",
            y="КоличествоСотрудников",
            title="Сотрудники по организациям",
        ),
    )


def test_count_by_category_bar_to_pie():
    df = pd.DataFrame(
        {
            "ГоловнаяОрганизация": ["ООО А", "ООО Б", "ООО В"],
            "КоличествоСотрудников": [10, 20, 5],
        }
    )
    plan = _plan_with_chart("bar")
    chart = optimize_chart_type(
        df,
        plan.chart,
        plan,
        user_query="Покажи график сотрудников по организациям",
    )
    assert chart.type == "pie"


def test_distribution_keywords_force_pie_for_sum():
    df = pd.DataFrame(
        {
            "Регион": ["Центр", "Юг", "Север"],
            "Сумма": [100, 200, 50],
        }
    )
    plan = AnalyticsPlan(
        queries=[AnalyticsSubQuery(alias="sales", entity="Document_Реализация", top=200)],
        aggregate=AggregateSpec(group_by=["Регион"], agg={"Сумма": "sum"}),
        chart=ChartSpec(type="bar", x="Регион", y="Сумма"),
    )
    chart = optimize_chart_type(
        df,
        plan.chart,
        plan,
        user_query="Покажи распределение продаж по регионам",
    )
    assert chart.type == "pie"


def test_top_n_keeps_bar():
    df = pd.DataFrame(
        {
            "Товар": [f"Товар {i}" for i in range(5)],
            "Количество": [10, 9, 8, 7, 6],
        }
    )
    plan = AnalyticsPlan(
        queries=[AnalyticsSubQuery(alias="items", entity="Catalog_Номенклатура", top=200)],
        aggregate=AggregateSpec(group_by=["Товар"], agg={"Количество": "count"}),
        chart=ChartSpec(type="bar", x="Товар", y="Количество"),
    )
    chart = optimize_chart_type(
        df,
        plan.chart,
        plan,
        user_query="Топ-5 товаров по количеству",
    )
    assert chart.type == "bar"


def test_temporal_axis_to_line():
    df = pd.DataFrame(
        {
            "Месяц": ["2024-01", "2024-02", "2024-03"],
            "Сумма": [100, 120, 90],
        }
    )
    plan = AnalyticsPlan(
        queries=[AnalyticsSubQuery(alias="sales", entity="Document_Реализация", top=200)],
        aggregate=AggregateSpec(group_by=["Месяц"], agg={"Сумма": "sum"}),
        chart=ChartSpec(type="bar", x="Месяц", y="Сумма"),
    )
    chart = optimize_chart_type(
        df,
        plan.chart,
        plan,
        user_query="Динамика продаж по месяцам",
    )
    assert chart.type == "line"


def test_pie_downgraded_when_too_many_categories():
    df = pd.DataFrame(
        {
            "Организация": [f"Org {i}" for i in range(12)],
            "Количество": list(range(12)),
        }
    )
    plan = AnalyticsPlan(
        queries=[AnalyticsSubQuery(alias="e", entity="Catalog_Сотрудники", top=200)],
        aggregate=AggregateSpec(group_by=["Организация"], agg={"Количество": "count"}),
        chart=ChartSpec(type="pie", x="Организация", y="Количество"),
    )
    chart = optimize_chart_type(df, plan.chart, plan)
    assert chart.type == "bar"


@pytest.mark.asyncio
async def test_analytics_executor_applies_chart_type_optimizer():
    executor = MagicMock()

    async def fake_execute(**kwargs):
        if kwargs["entity"] == "Catalog_Организации":
            return (
                [
                    {"Ref_Key": "53491e4a-816b-11eb-9e27-005056aa26c4", "Description": "ООО А"},
                    {"Ref_Key": "2dbb0da7-5e3b-11ef-b2cc-005056bdafd6", "Description": "ООО Б"},
                ],
                2,
            )
        return (
            [
                {"ГоловнаяОрганизация_Key": "53491e4a-816b-11eb-9e27-005056aa26c4"},
                {"ГоловнаяОрганизация_Key": "53491e4a-816b-11eb-9e27-005056aa26c4"},
                {"ГоловнаяОрганизация_Key": "2dbb0da7-5e3b-11ef-b2cc-005056bdafd6"},
            ],
            3,
        )

    executor.execute = AsyncMock(side_effect=fake_execute)

    plan = _plan_with_chart("bar")
    analytics = AnalyticsExecutor(executor, max_records=200, max_joins=3)
    result = await analytics.execute(
        plan,
        user_query="Покажи график сотрудников по организациям",
    )

    assert result.plan.chart is not None
    assert result.plan.chart.type == "pie"
