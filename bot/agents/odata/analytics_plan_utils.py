#!/usr/bin/env python3
"""Нормализация analytics-плана от AI (имена колонок, $expand для ссылок)."""

from __future__ import annotations

import re

import pandas as pd

from bot.agents.odata.analytics_models import AnalyticsPlan, AnalyticsSubQuery

_KEY_SUFFIX = "_Key"


def normalize_analytics_plan(plan: AnalyticsPlan) -> AnalyticsPlan:
    """Привести план к именам колонок DataFrame и добавить $expand для ссылок."""
    aliases = {q.alias for q in plan.queries}
    reference_bases: set[str] = set()

    for query in plan.queries:
        reference_bases |= _collect_reference_bases_from_select(query.select)

    if plan.aggregate:
        normalized_group_by: list[str] = []
        for field in plan.aggregate.group_by:
            column, expands = _analytics_column_name(field, aliases)
            reference_bases |= expands
            normalized_group_by.append(column)
        plan.aggregate.group_by = normalized_group_by
        plan.aggregate.agg = {
            _analytics_column_name(key, aliases)[0]: value for key, value in plan.aggregate.agg.items()
        }

    if plan.chart:
        column_x, expands_x = _analytics_column_name(plan.chart.x, aliases)
        reference_bases |= expands_x
        plan.chart.x = column_x
        if plan.chart.y:
            plan.chart.y = _analytics_column_name(plan.chart.y, aliases)[0]

    for query in plan.queries:
        query.expand = _merge_expand(query.expand, reference_bases)
        _enrich_query_select_for_references(query, reference_bases)

    return plan


def align_dataframe_to_plan(df: pd.DataFrame, plan: AnalyticsPlan) -> pd.DataFrame:
    """Подогнать имена колонок DataFrame под нормализованный analytics-план."""
    if df.empty:
        return df

    fields: list[str] = []
    if plan.aggregate:
        fields.extend(plan.aggregate.group_by)
        fields.extend(plan.aggregate.agg.keys())
    if plan.chart:
        fields.append(plan.chart.x)
        if plan.chart.y:
            fields.append(plan.chart.y)

    renames: dict[str, str] = {}
    columns = set(df.columns)
    for field in fields:
        if not field or field in columns or field in renames.values():
            continue
        key_column = f"{field}{_KEY_SUFFIX}"
        if key_column in columns and key_column not in renames:
            renames[key_column] = field
            continue
        for suffix in ("Description", "Code", "НаименованиеПолное"):
            nested = f"{field}_{suffix}"
            if nested in columns and nested not in renames:
                renames[nested] = field
                break
        else:
            odata_flat = field.replace("/", "_")
            if odata_flat in columns and odata_flat not in renames:
                renames[odata_flat] = field

    return df.rename(columns=renames) if renames else df


def _analytics_column_name(field: str, aliases: set[str]) -> tuple[str, set[str]]:
    """OData/alias-имя поля → имя колонки DataFrame и навигации для $expand."""
    field = _normalize_field_name(field, aliases)
    expands: set[str] = set()

    if "/" in field:
        base, _sub = field.split("/", 1)
        if base.endswith(_KEY_SUFFIX):
            base = base[: -len(_KEY_SUFFIX)]
        expands.add(base)
        return base, expands

    if field.endswith(_KEY_SUFFIX):
        base = field[: -len(_KEY_SUFFIX)]
        expands.add(base)
        return base, expands

    return field, expands


def _normalize_field_name(name: str, aliases: set[str]) -> str:
    if "/" not in name:
        return name
    prefix, rest = name.split("/", 1)
    if prefix in aliases:
        return rest
    return name


def _collect_reference_bases_from_select(select: str | None) -> set[str]:
    if not select:
        return set()
    bases: set[str] = set()
    for part in select.split(","):
        field = part.strip()
        if field.endswith(_KEY_SUFFIX):
            bases.add(field[: -len(_KEY_SUFFIX)])
    return bases


def _merge_expand(existing: str | None, nav_props: set[str]) -> str | None:
    parts: list[str] = []
    if existing:
        for item in re.split(r",(?![^(]*\))", existing):
            normalized = _normalize_expand_token(item.strip())
            if normalized and normalized not in parts:
                parts.append(normalized)
    for prop in sorted(nav_props):
        entry = _normalize_expand_token(prop)
        if entry and entry not in parts:
            parts.append(entry)
    return ",".join(parts) if parts else None


def _normalize_expand_token(nav_prop: str) -> str:
    """1С OData: $expand — только имя навигации, без ($select=...)."""
    token = nav_prop.strip()
    if not token:
        return ""
    if "($select=" in token:
        token = token.split("(", 1)[0]
    if "/" in token:
        token = token.split("/", 1)[0]
    if token.endswith(_KEY_SUFFIX):
        token = token[: -len(_KEY_SUFFIX)]
    return token


def _enrich_query_select_for_references(query: AnalyticsSubQuery, nav_props: set[str]) -> None:
    """Подготовить $select для ссылочных полей (только *_Key, без Nav/Field)."""
    if not nav_props:
        return

    parts: list[str] = []
    if query.select:
        parts.extend(item.strip() for item in query.select.split(",") if item.strip())

    # 1С: поля Nav/Description в $select конфликтуют с $expand — убираем
    parts = [_strip_nav_select_path(part, nav_props) for part in parts]
    parts = [part for part in parts if part]

    for nav in sorted(nav_props):
        key_field = f"{nav}_Key"
        if key_field not in parts:
            parts.append(key_field)

    deduped: list[str] = []
    for part in parts:
        if part not in deduped:
            deduped.append(part)
    query.select = ",".join(deduped)


def _strip_nav_select_path(field: str, nav_props: set[str]) -> str:
    """Убрать Nav/Subfield из select — представление берём из $expand или lookup."""
    if "/" not in field:
        return field
    base, _sub = field.split("/", 1)
    if base in nav_props or base.endswith(_KEY_SUFFIX) and base[: -len(_KEY_SUFFIX)] in nav_props:
        key_field = base if base.endswith(_KEY_SUFFIX) else f"{base}{_KEY_SUFFIX}"
        return key_field
    return field


def enrich_subquery_expand(query: AnalyticsSubQuery, nav_props: set[str]) -> None:
    """Добавить навигационные свойства в $expand подзапроса."""
    query.expand = _merge_expand(query.expand, nav_props)
