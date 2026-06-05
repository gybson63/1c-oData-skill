#!/usr/bin/env python3
"""Тесты analytics_reference_labels.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from bot.agents.odata.analytics_executor import AnalyticsExecutor
from bot.agents.odata.analytics_models import AnalyticsPlan
from bot.agents.odata.analytics_plan_utils import normalize_analytics_plan
from bot.agents.odata.analytics_reference_labels import (
    build_presentation_select,
    guess_catalog_entity,
    is_guid,
    presentation_select_fallbacks,
    resolve_reference_labels_in_dataframe,
)


def test_is_guid():
    assert is_guid("53491e4a-816b-11eb-9e27-005056aa26c4")
    assert not is_guid("ООО Альфа")


def test_guess_catalog_entity():
    assert guess_catalog_entity("ГоловнаяОрганизация") == "Catalog_Организации"


def test_build_presentation_select_skips_missing_code():
    metadata = MagicMock()
    metadata.get_entity_fields.return_value = [
        "Ref_Key",
        "Description",
        "НаименованиеПолное",
        "DeletionMark",
    ]
    select = build_presentation_select("Catalog_Организации", metadata)
    assert select == "Ref_Key,Description,НаименованиеПолное"
    assert "Code" not in select


def test_presentation_select_fallbacks():
    metadata = MagicMock()
    metadata.get_entity_fields.return_value = ["Ref_Key", "Description", "НаименованиеПолное"]
    fallbacks = presentation_select_fallbacks("Catalog_Организации", metadata)
    assert fallbacks[0] == "Ref_Key,Description,НаименованиеПолное"
    assert "Ref_Key,Description" in fallbacks
    assert fallbacks[-1] is None


def test_normalize_expand_simple_for_1c():
    plan = normalize_analytics_plan(
        AnalyticsPlan.from_dict(
            {
                "mode": "analytics",
                "queries": [
                    {
                        "alias": "employees",
                        "entity": "Catalog_Сотрудники",
                        "select": "ГоловнаяОрганизация_Key",
                        "expand": "ГоловнаяОрганизация($select=Description,Code)",
                        "top": 200,
                    }
                ],
                "aggregate": {
                    "group_by": ["ГоловнаяОрганизация_Key"],
                    "agg": {"Количество": "count"},
                },
                "chart": {"type": "bar", "x": "ГоловнаяОрганизация_Key", "y": "Количество"},
            }
        )
    )
    assert plan.queries[0].expand == "ГоловнаяОрганизация"
    assert "($select=" not in (plan.queries[0].expand or "")
    assert "ГоловнаяОрганизация/Description" not in (plan.queries[0].select or "")
    assert "ГоловнаяОрганизация_Key" in (plan.queries[0].select or "")


def test_normalize_strips_nav_description_from_select():
    plan = normalize_analytics_plan(
        AnalyticsPlan.from_dict(
            {
                "mode": "analytics",
                "queries": [
                    {
                        "alias": "employees",
                        "entity": "Catalog_Сотрудники",
                        "select": "ГоловнаяОрганизация_Key,ГоловнаяОрганизация/Description",
                        "top": 200,
                    }
                ],
                "aggregate": {
                    "group_by": ["ГоловнаяОрганизация/Description"],
                    "agg": {"Количество": "count"},
                },
                "chart": {"type": "bar", "x": "ГоловнаяОрганизация/Description", "y": "Количество"},
            }
        )
    )
    assert plan.queries[0].select == "ГоловнаяОрганизация_Key"
    assert plan.queries[0].expand == "ГоловнаяОрганизация"


@pytest.mark.asyncio
async def test_resolve_reference_labels_in_dataframe():
    executor = MagicMock()
    executor.execute = AsyncMock(
        return_value=(
            [
                {
                    "Ref_Key": "53491e4a-816b-11eb-9e27-005056aa26c4",
                    "Description": "ООО Первый БИТ",
                }
            ],
            1,
        )
    )

    df = pd.DataFrame(
        {
            "ГоловнаяОрганизация": [
                "53491e4a-816b-11eb-9e27-005056aa26c4",
                "53491e4a-816b-11eb-9e27-005056aa26c4",
            ],
            "Количество": [10, 20],
        }
    )

    result = await resolve_reference_labels_in_dataframe(
        df,
        ["ГоловнаяОрганизация"],
        executor,
    )

    assert result["ГоловнаяОрганизация"].iloc[0] == "ООО Первый БИТ"
    executor.execute.assert_called_once()
    call_kwargs = executor.execute.call_args.kwargs
    assert "Code" not in (call_kwargs.get("select") or "")


@pytest.mark.asyncio
async def test_analytics_executor_resolves_guid_labels_before_aggregate():
    executor = MagicMock()

    async def fake_execute(**kwargs):
        entity = kwargs["entity"]
        if entity == "Catalog_Организации":
            guid = "53491e4a-816b-11eb-9e27-005056aa26c4"
            return ([{"Ref_Key": guid, "Description": "ООО Первый БИТ"}], 1)
        return (
            [
                {"ГоловнаяОрганизация_Key": "53491e4a-816b-11eb-9e27-005056aa26c4"},
                {"ГоловнаяОрганизация_Key": "53491e4a-816b-11eb-9e27-005056aa26c4"},
                {"ГоловнаяОрганизация_Key": "2dbb0da7-5e3b-11ef-b2cc-005056bdafd6"},
            ],
            3,
        )

    executor.execute = AsyncMock(side_effect=fake_execute)

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
                "group_by": ["ГоловнаяОрганизация_Key"],
                "agg": {"КоличествоСотрудников": "count"},
            },
            "chart": {
                "type": "bar",
                "x": "ГоловнаяОрганизация_Key",
                "y": "КоличествоСотрудников",
            },
        }
    )

    analytics = AnalyticsExecutor(executor, max_records=200, max_joins=3)
    result = await analytics.execute(plan)

    orgs = set(result.dataframe["ГоловнаяОрганизация"].tolist())
    assert "ООО Первый БИТ" in orgs
    assert "53491e4a-816b-11eb-9e27-005056aa26c4" not in orgs
