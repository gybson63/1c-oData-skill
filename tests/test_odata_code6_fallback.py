#!/usr/bin/env python3
"""Тесты fallback OData code 6 и нормализации select."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from bot.agents.odata.field_aliases import (
    is_virtual_table_entity,
    normalize_nav_select,
    strip_virtual_table_suffix,
)
from bot.agents.odata.odata_query_utils import (
    dedupe_records_by_field,
    is_odata_code6_extra_segments,
    key_columns_from_select,
    slice_last_record_type_entity,
)
from bot.agents.odata.query_executor import QueryExecutor
from bot.agents.odata.query_validator import QueryValidator
from bot_lib.exceptions import ODataHTTPError


def test_normalize_nav_select_to_key():
    raw = "Сотрудник/Description,Должность/Description,Подразделение/Description"
    assert normalize_nav_select(raw) == ("Сотрудник_Key,Должность_Key,ПодразделениеОрганизации_Key")


def test_is_virtual_table_entity():
    assert is_virtual_table_entity("InformationRegister_X/SliceLast()")
    assert not is_virtual_table_entity("Catalog_Сотрудники")


def test_strip_virtual_table_suffix():
    assert strip_virtual_table_suffix("InformationRegister_A/SliceLast()") == "InformationRegister_A"


def test_is_odata_code6_extra_segments():
    err = ODataHTTPError(
        'HTTP 400 {"odata.error":{"code":"6","message":{"value":"Обнаружены лишние сегменты!"}}}',
        status_code=400,
    )
    assert is_odata_code6_extra_segments(err)


def test_key_columns_from_select():
    cols = key_columns_from_select("Сотрудник_Key,Description,Должность_Key")
    assert cols == ["Сотрудник_Key", "Должность_Key"]


def test_validator_omits_expand_for_virtual_table():
    class Meta:
        def get_entity_fields(self, entity: str) -> list[str]:
            return ["Сотрудник_Key", "Должность_Key"]

    class Q:
        entity = "InformationRegister_КадроваяИсторияСотрудников/SliceLast()"
        filter_expr = None
        select = "Сотрудник_Key,Должность_Key"
        orderby = None
        top = 10
        skip = None

    v = QueryValidator(metadata=Meta(), odata_url="http://localhost/odata")
    out = v.validate(Q())
    assert out["expand"] is None
    assert "Сотрудник_Key" in (out["select"] or "")


@pytest.mark.asyncio
async def test_code6_fallback_without_expand():
    executor = QueryExecutor("http://localhost/odata", "Basic x")
    code6 = ODataHTTPError(
        'HTTP 400 {"odata.error":{"code":"6","message":{"value":"лишние сегменты"}}}',
        status_code=400,
    )
    ok_records = [{"Сотрудник_Key": "00000000-0000-0000-0000-000000000001"}]

    with patch(
        "bot.agents.odata.query_executor.execute_odata_query",
        new_callable=AsyncMock,
    ) as mock_exec:
        mock_exec.side_effect = [
            code6,
            (ok_records, 1),
        ]
        with patch.object(
            executor,
            "_maybe_resolve_key_labels",
            new_callable=AsyncMock,
            return_value=ok_records,
        ):
            with patch.object(
                executor,
                "_resolve_key_labels",
                new_callable=AsyncMock,
                return_value=ok_records,
            ):
                records, total = await executor.execute(
                    entity="InformationRegister_X/SliceLast()",
                    select="Сотрудник_Key",
                    expand="Сотрудник",
                    top=10,
                )

    assert total == 1
    assert mock_exec.call_count == 2
    assert mock_exec.call_args_list[1].kwargs.get("expand") is None


@pytest.mark.asyncio
async def test_code6_fallback_without_inlinecount():
    executor = QueryExecutor("http://localhost/odata", "Basic x")
    code6 = ODataHTTPError(
        'HTTP 400 {"odata.error":{"code":"6","message":{"value":"лишние сегменты"}}}',
        status_code=400,
    )
    ok_records = [{"Сотрудник_Key": "guid"}]

    with patch(
        "bot.agents.odata.query_executor.execute_odata_query",
        new_callable=AsyncMock,
    ) as mock_exec:
        mock_exec.side_effect = [
            code6,
            code6,
            (ok_records, 1),
        ]
        with patch.object(
            executor,
            "_maybe_resolve_key_labels",
            new_callable=AsyncMock,
            return_value=ok_records,
        ):
            with patch.object(
                executor,
                "_resolve_key_labels",
                new_callable=AsyncMock,
                return_value=ok_records,
            ):
                records, total = await executor.execute(
                    entity="InformationRegister_X/SliceLast()",
                    select="Сотрудник_Key",
                    expand="Сотрудник",
                    top=5,
                )

    assert total == 1
    assert mock_exec.call_count == 3
    assert mock_exec.call_args_list[2].kwargs.get("inline_count") is False


def test_slice_last_record_type_entity():
    entity = "InformationRegister_КадроваяИсторияСотрудников/SliceLast()"
    assert slice_last_record_type_entity(entity) == ("InformationRegister_КадроваяИсторияСотрудников_RecordType")


def test_dedupe_records_by_field():
    rows = [
        {"Сотрудник_Key": "a", "n": 1},
        {"Сотрудник_Key": "b", "n": 2},
        {"Сотрудник_Key": "a", "n": 3},
    ]
    out = dedupe_records_by_field(rows, "Сотрудник_Key", limit=2)
    assert len(out) == 2
    assert out[0]["n"] == 1


@pytest.mark.asyncio
async def test_code6_fallback_to_record_type():
    class Meta:
        def get_entity_fields(self, entity: str) -> list[str]:
            return ["Сотрудник_Key", "Должность_Key", "Подразделение_Key", "Period"]

    code6 = ODataHTTPError(
        'HTTP 400 {"odata.error":{"code":"6","message":{"value":"лишние сегменты"}}}',
        status_code=400,
    )
    rt_rows = [
        {"Сотрудник_Key": "a", "Period": "2026-01-01"},
        {"Сотрудник_Key": "a", "Period": "2025-01-01"},
        {"Сотрудник_Key": "b", "Period": "2026-01-01"},
    ]
    executor = QueryExecutor("http://localhost/odata", "Basic x", metadata=Meta())

    with patch(
        "bot.agents.odata.query_executor.execute_odata_query",
        new_callable=AsyncMock,
    ) as mock_exec:
        mock_exec.side_effect = [code6, code6, (rt_rows, 3)]
        with patch.object(
            executor,
            "_maybe_resolve_key_labels",
            new_callable=AsyncMock,
            side_effect=lambda r, _s: r,
        ):
            with patch.object(
                executor,
                "_resolve_key_labels",
                new_callable=AsyncMock,
                side_effect=lambda r, _s: r,
            ):
                records, total = await executor.execute(
                    entity="InformationRegister_КадроваяИсторияСотрудников/SliceLast()",
                    select="Сотрудник_Key,Должность_Key,ПодразделениеОрганизации_Key",
                    top=2,
                )

    assert total == 2
    assert len(records) == 2
    assert mock_exec.call_count == 3
    assert mock_exec.call_args_list[2].kwargs["entity"].endswith("_RecordType")
    assert mock_exec.call_args_list[2].kwargs["orderby"] == "Period desc"
