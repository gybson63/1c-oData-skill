#!/usr/bin/env python3
"""Тесты metadata-aware $expand."""

from __future__ import annotations

from bot.agents.odata.field_aliases import resolve_nav_property
from bot.agents.odata.query_builder import build_expand


def test_build_expand_podrazdelenie_not_organizacii() -> None:
    fields = ["Date", "Подразделение_Key", "Организация_Key", "Подразделение", "Организация"]
    expand = build_expand(
        "Document_НачислениеЗарплаты",
        "Date,Подразделение_Key,Организация_Key",
        fields,
    )
    assert expand == "Организация,Подразделение"


def test_resolve_nav_property_reverse_alias() -> None:
    available = frozenset({"Подразделение", "Сотрудник"})
    assert resolve_nav_property("ПодразделениеОрганизации", available) == "Подразделение"
