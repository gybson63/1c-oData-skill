#!/usr/bin/env python3
"""Тесты response_parser."""

from bot.agents.odata.response_parser import resolve_references


def test_resolve_references_keeps_post_resolved_labels_in_key_columns():
    records = [
        {
            "Сотрудник_Key": "Иванов Иван",
            "Должность_Key": "Бухгалтер",
            "Подразделение_Key": "Отдел кадров",
        }
    ]
    out = resolve_references(records)
    assert out == [
        {
            "Сотрудник": "Иванов Иван",
            "Должность": "Бухгалтер",
            "Подразделение": "Отдел кадров",
        }
    ]


def test_resolve_references_still_drops_unexpanded_guids():
    guid = "00000000-0000-0000-0000-000000000001"
    records = [{"Сотрудник_Key": guid}]
    out = resolve_references(records)
    assert out == [{}]
