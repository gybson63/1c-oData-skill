#!/usr/bin/env python3
"""Тесты analytics_executor."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.agents.odata.analytics_executor import AnalyticsExecutor
from bot.agents.odata.analytics_models import AnalyticsPlan, AnalyticsSubQuery, JoinSpec


@pytest.mark.asyncio
async def test_analytics_executor_single_query():
    executor = MagicMock()
    executor.execute = AsyncMock(
        return_value=(
            [{"Контрагент": "ООО А", "Сумма": 100}, {"Контрагент": "ООО Б", "Сумма": 200}],
            2,
        )
    )

    plan = AnalyticsPlan(
        queries=[
            AnalyticsSubQuery(alias="sales", entity="Document_Реализация", top=50),
        ],
    )

    analytics = AnalyticsExecutor(executor, max_records=100, max_joins=3)
    result = await analytics.execute(plan)

    assert len(result.dataframe) == 2
    assert "Контрагент" in result.dataframe.columns
    assert result.plan.queries[0].entity == "Document_Реализация"
    executor.execute.assert_called_once()


@pytest.mark.asyncio
async def test_analytics_executor_join():
    executor = MagicMock()

    async def fake_execute(**kwargs):
        entity = kwargs["entity"]
        if "Номенклатура" in entity:
            return ([{"Description": "Товар А", "Code": "001"}], 1)
        return ([{"Номенклатура": "Товар А", "Сумма": 500}], 1)

    executor.execute = AsyncMock(side_effect=fake_execute)

    plan = AnalyticsPlan(
        queries=[
            AnalyticsSubQuery(alias="lines", entity="Document_Реализация_Товары", top=50),
            AnalyticsSubQuery(alias="items", entity="Catalog_Номенклатура", top=50),
        ],
        joins=[
            JoinSpec(
                left="lines",
                right="items",
                left_on="Номенклатура",
                right_on="Description",
                how="inner",
            )
        ],
    )

    analytics = AnalyticsExecutor(executor, max_records=100, max_joins=3)
    result = await analytics.execute(plan)

    assert len(result.dataframe) == 1
    assert result.row_counts["lines"] == 1
    assert result.row_counts["items"] == 1
