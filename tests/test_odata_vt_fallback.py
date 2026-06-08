#!/usr/bin/env python3
"""Тесты fallback виртуальных таблиц и IR → RecordType."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from bot.agents.odata.odata_query_utils import (
    information_register_record_type_entity,
    is_odata_code6_method_not_found,
    is_odata_code6_segment_not_found,
    next_accumulation_vt_entity,
)
from bot.agents.odata.query_executor import QueryExecutor
from bot_lib.exceptions import ODataHTTPError


def test_next_accumulation_vt_entity_balance_to_turnovers():
    exc = ODataHTTPError(
        'HTTP 400 {"odata.error":{"code":"6","message":{"value":"Метод не найден"}}}',
        status_code=400,
    )
    entity = "AccumulationRegister_ФактическиеОтпуска/Balance()"
    assert next_accumulation_vt_entity(entity, exc) == "AccumulationRegister_ФактическиеОтпуска/Turnovers()"


def test_next_accumulation_vt_entity_noop_for_turnovers():
    exc = ODataHTTPError(
        'HTTP 400 {"odata.error":{"code":"6","message":{"value":"Метод не найден"}}}',
        status_code=400,
    )
    entity = "AccumulationRegister_ФактическиеОтпуска/Turnovers()"
    assert next_accumulation_vt_entity(entity, exc) is None


def test_information_register_record_type_entity():
    assert (
        information_register_record_type_entity("InformationRegister_НачальныеОстаткиОтпусков")
        == "InformationRegister_НачальныеОстаткиОтпусков_RecordType"
    )
    assert information_register_record_type_entity("InformationRegister_X/SliceLast()") is None


def test_is_odata_code6_method_not_found():
    exc = ODataHTTPError(
        'HTTP 400 {"odata.error":{"code":"6","message":{"value":"Метод не найден"}}}',
        status_code=400,
    )
    assert is_odata_code6_method_not_found(exc)


def test_is_odata_code6_segment_not_found():
    exc = ODataHTTPError(
        'HTTP 400 {"odata.error":{"code":"6","message":{"value":"Сегмент пути Сотрудник_Key не найден!"}}}',
        status_code=400,
    )
    assert is_odata_code6_segment_not_found(exc)


@pytest.mark.asyncio
async def test_balance_fallback_to_turnovers():
    executor = QueryExecutor("http://localhost/odata", "Basic x")
    method_err = ODataHTTPError(
        'HTTP 400 {"odata.error":{"code":"6","message":{"value":"Метод не найден"}}}',
        status_code=400,
    )
    ok_records = [{"Сотрудник_Key": "guid", "КоличествоTurnover": 2}]

    with patch(
        "bot.agents.odata.query_executor.execute_odata_query",
        new_callable=AsyncMock,
    ) as mock_exec:
        mock_exec.side_effect = [method_err, (ok_records, 1)]
        with patch.object(
            executor,
            "_maybe_resolve_key_labels",
            new_callable=AsyncMock,
            return_value=ok_records,
        ):
            records, total = await executor.execute(
                entity="AccumulationRegister_ФактическиеОтпуска/Balance()",
                select="Сотрудник_Key,КоличествоTurnover",
                top=5,
            )

    assert total == 1
    assert mock_exec.call_count == 2
    assert "Turnovers()" in mock_exec.call_args_list[1].kwargs["entity"]


@pytest.mark.asyncio
async def test_ir_header_fallback_to_record_type():
    class Meta:
        def get_entity_fields(self, entity: str) -> list[str]:
            return [
                "Сотрудник_Key",
                "ВидЕжегодногоОтпуска_Key",
                "КоличествоДней",
                "ДатаОстатка",
            ]

    segment_err = ODataHTTPError(
        'HTTP 400 {"odata.error":{"code":"6","message":{"value":"Сегмент пути Сотрудник_Key не найден!"}}}',
        status_code=400,
    )
    rt_rows = [
        {"Сотрудник_Key": "a", "ДатаОстатка": "2021-03-01", "КоличествоДней": 28},
        {"Сотрудник_Key": "b", "ДатаОстатка": "2021-03-01", "КоличествоДней": 14},
    ]
    executor = QueryExecutor("http://localhost/odata", "Basic x", metadata=Meta())

    with patch(
        "bot.agents.odata.query_executor.execute_odata_query",
        new_callable=AsyncMock,
    ) as mock_exec:
        mock_exec.side_effect = [segment_err, (rt_rows, 2)]
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
                    entity="InformationRegister_НачальныеОстаткиОтпусков",
                    select="Сотрудник_Key,ВидЕжегодногоОтпуска_Key,КоличествоДней",
                    top=2,
                )

    assert total == 2
    assert mock_exec.call_args_list[1].kwargs["entity"].endswith("_RecordType")
    assert mock_exec.call_args_list[1].kwargs["orderby"] == "ДатаОстатка desc"
