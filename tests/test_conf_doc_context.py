#!/usr/bin/env python3
"""Тесты обогащения промпта через conf-doc."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from bot.agents.odata.conf_doc_context import (
    build_conf_doc_search_queries,
    collect_report_drilldown_targets,
    fetch_conf_doc_context,
    fetch_report_skd_context,
    filter_search_results,
    format_report_skd_block,
    format_search_results,
    merge_search_results,
    select_skd_related_chunks,
)
from bot.config import ConfDocSettings


def test_format_search_results_empty() -> None:
    assert format_search_results([]) == ""


def test_format_search_results_with_odata_mapping() -> None:
    block = format_search_results(
        [
            {
                "object_type": "Document",
                "name": "Отпуск",
                "synonym": "Отпуск",
                "score": 0.92,
                "text": "Документ для регистрации отпусков сотрудников.",
            }
        ]
    )
    assert "Document.Отпуск" in block
    assert "OData: Document_Отпуск" in block
    assert "отпусков" in block


@pytest.mark.asyncio
async def test_fetch_conf_doc_context_disabled() -> None:
    settings = ConfDocSettings(enabled=False)
    result = await fetch_conf_doc_context("отпуск", settings)
    assert result == ""


def test_build_conf_doc_search_queries_employees() -> None:
    queries = build_conf_doc_search_queries(
        "Покажи 10 штатных сотрудников: ФИО, должность и подразделение",
        request_brief="Штатные сотрудники: ФИО, должность, подразделение",
    )
    assert "Штатные сотрудники" in queries[0]
    assert "КадроваяИсторияСотрудников" in queries
    assert "Сотрудники" in queries


def test_build_conf_doc_search_queries_vacation_balance() -> None:
    queries = build_conf_doc_search_queries(
        "Сколько дней отпуска осталось у сотрудников?",
    )
    assert "АналитикаОстатковОтпусков" in queries
    assert "ФактическиеОтпуска" in queries
    assert "НачальныеОстаткиОтпусков" in queries
    assert "ОстаткиОтпусков" in queries


def test_build_conf_doc_search_queries_salary() -> None:
    queries = build_conf_doc_search_queries(
        "Покажи начисления зарплаты за апрель 2025",
    )
    assert "НачислениеЗарплаты" in queries
    assert "начисление зарплаты" in queries


def test_select_skd_related_chunks_includes_split_continuations() -> None:
    chunks = [
        (0, "# Overview"),
        (1, "## Модуль объекта\n```bsl\nПроцедура Тест()\n```"),
        (2, "## Запрос СКД: Набор1\nВЫБРАТЬ ИЗ РегистрСведений.НачальныеОстаткиОтпусков"),
        (3, "продолжение без заголовка\nОБЪЕДИНИТЬ РегистрНакопления.ФактическиеОтпуска"),
        (4, "хвост запроса\nВЫБРАТЬ 1"),
    ]
    selected = select_skd_related_chunks(chunks)
    indices = [idx for idx, _ in selected]
    assert 1 in indices
    assert 2 in indices
    assert 3 in indices
    assert 4 in indices
    assert 0 not in indices


def test_collect_report_drilldown_targets_domain_and_search() -> None:
    batches = [
        [
            {"object_type": "Report", "name": "ОстаткиОтпусков", "score": 0.31},
            {"object_type": "InformationRegister", "name": "АналитикаОстатковОтпусков", "score": 0.95},
        ],
        [
            {"object_type": "Report", "name": "АнализНачислений", "score": 0.42},
        ],
    ]
    targets = collect_report_drilldown_targets(
        "Сколько дней отпуска осталось?",
        batches,
    )
    assert ("Report", "ОстаткиОтпусков") in targets
    assert targets[0] == ("Report", "ОстаткиОтпусков")


def test_collect_report_drilldown_targets_from_search_only() -> None:
    batches = [
        [
            {"object_type": "Report", "name": "АнализНачислений", "score": 0.55},
            {"object_type": "Document", "name": "НачислениеЗарплаты", "score": 0.9},
        ],
    ]
    targets = collect_report_drilldown_targets(
        "Покажи начисления зарплаты за апрель",
        batches,
    )
    assert targets == [("Report", "АнализНачислений")]


def test_format_report_skd_block_extracts_registers() -> None:
    text = (
        "## Запрос СКД: Основной\n\n"
        "ВЫБРАТЬ ... ИЗ РегистрСведений.НачальныеОстаткиОтпусков\n"
        "ОБЪЕДИНИТЬ РегистрНакопления.ФактическиеОтпуска"
    )
    block = format_report_skd_block("Report", "ОстаткиОтпусков", [(2, text)])
    assert "Report.ОстаткиОтпусков" in block
    assert "НачальныеОстаткиОтпусков" in block
    assert "ФактическиеОтпуска" in block


@pytest.mark.asyncio
async def test_fetch_report_skd_context_reads_skd_chunks() -> None:
    mock_client = AsyncMock()
    mock_client.get_object = AsyncMock(
        return_value={
            "chunks": [
                {"chunk_index": 0},
                {"chunk_index": 1},
                {"chunk_index": 2},
                {"chunk_index": 3},
            ]
        }
    )
    mock_client.get_object_chunk = AsyncMock(
        side_effect=[
            {"text": "# Overview"},
            {"text": "## Модуль объекта\n```bsl\nПроцедура Тест()\n```"},
            {"text": ("## Запрос СКД: Набор1\nИЗ РегистрСведений.АналитикаОстатковОтпусков")},
            {"text": ("продолжение\nОБЪЕДИНИТЬ РегистрНакопления.ФактическиеОтпуска")},
        ]
    )
    block = await fetch_report_skd_context(mock_client, "Report", "ОстаткиОтпусков")
    assert "Запрос СКД" in block
    assert "АналитикаОстатковОтпусков" in block
    assert "ФактическиеОтпуска" in block
    assert mock_client.get_object_chunk.await_count == 4


def test_filter_search_results_prefers_odata_types() -> None:
    results = filter_search_results(
        [
            {"object_type": "Role", "name": "X", "score": 0.9},
            {"object_type": "Catalog", "name": "Сотрудники", "score": 0.7},
        ]
    )
    assert len(results) == 1
    assert results[0]["name"] == "Сотрудники"


def test_merge_search_results_dedupes() -> None:
    merged = merge_search_results(
        [
            [{"object_type": "Catalog", "name": "Сотрудники", "score": 0.8}],
            [{"object_type": "Catalog", "name": "Сотрудники", "score": 0.9}],
        ],
        top_k=3,
    )
    assert len(merged) == 1


@pytest.mark.asyncio
async def test_fetch_conf_doc_context_includes_report_skd() -> None:
    settings = ConfDocSettings(
        enabled=True,
        api_url="http://localhost:8050",
        configuration="ЗарплатаИУправлениеПерсоналомКОРП",
        enrich_prompt=True,
        search_top_k=3,
    )
    mock_client = AsyncMock()
    mock_client.health = AsyncMock(return_value={"status": "ok"})
    mock_client.search = AsyncMock(
        return_value=[
            {
                "object_type": "InformationRegister",
                "name": "АналитикаОстатковОтпусков",
                "score": 0.9,
                "text": "Регистр остатков отпусков.",
            },
            {
                "object_type": "Report",
                "name": "ОстаткиОтпусков",
                "score": 0.3,
                "text": "Отчёт остатки отпусков.",
            },
        ]
    )
    mock_client.get_object = AsyncMock(return_value={"chunks": [{"chunk_index": 2}]})
    mock_client.get_object_chunk = AsyncMock(
        return_value={"text": ("## Запрос СКД: Набор1\nИЗ РегистрНакопления.ФактическиеОтпуска")}
    )

    with patch("bot.agents.odata.conf_doc_context.ConfDocClient", return_value=mock_client):
        result = await fetch_conf_doc_context(
            "Сколько дней отпуска осталось у сотрудников?",
            settings,
        )

    assert "InformationRegister.АналитикаОстатковОтпусков" in result
    assert "Report.ОстаткиОтпусков" in result
    assert "ФактическиеОтпуска" in result


@pytest.mark.asyncio
async def test_fetch_conf_doc_context_success() -> None:
    settings = ConfDocSettings(
        enabled=True,
        api_url="http://localhost:8050",
        configuration="ЗарплатаИУправлениеПерсоналомКОРП",
        enrich_prompt=True,
        search_top_k=3,
    )
    mock_client = AsyncMock()
    mock_client.health = AsyncMock(return_value={"status": "ok"})
    mock_client.search = AsyncMock(
        return_value=[
            {
                "object_type": "Catalog",
                "name": "Сотрудники",
                "synonym": "Сотрудники",
                "score": 0.8,
                "text": "Справочник сотрудников.",
            }
        ]
    )

    with patch("bot.agents.odata.conf_doc_context.ConfDocClient", return_value=mock_client):
        result = await fetch_conf_doc_context("сотрудники", settings)

    assert "Catalog.Сотрудники" in result
    assert "OData: Catalog_Сотрудники" in result
    mock_client.health.assert_awaited_once()
    mock_client.search.assert_awaited()


@pytest.mark.asyncio
async def test_fetch_conf_doc_context_api_unavailable() -> None:
    from bot_lib.conf_doc_client import ConfDocApiError

    settings = ConfDocSettings(enabled=True, api_url="http://localhost:8050", enrich_prompt=True)
    mock_client = AsyncMock()
    mock_client.health = AsyncMock(side_effect=ConfDocApiError("connection refused"))

    with patch("bot.agents.odata.conf_doc_context.ConfDocClient", return_value=mock_client):
        result = await fetch_conf_doc_context("отпуск", settings)

    assert result == ""
