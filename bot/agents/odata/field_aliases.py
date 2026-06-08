#!/usr/bin/env python3
"""Нормализация имён полей OData (алиасы конфигурации ЗУП и др.)."""

from __future__ import annotations

import re
from typing import Any

# Виртуальные таблицы регистров в OData (суффикс entity).
_VT_SUFFIX_RE = re.compile(
    r"/(SliceLast|SliceFirst|Balance|Turnovers|BalanceAndTurnovers|"
    r"ScheduleData|ActualActionPeriod|Recalculation|Base)\([^)]*\)$",
)
# Nav/Property в $select → Nav_Key (1С часто отвечает code 6 «лишние сегменты»).
_NAV_SELECT_RE = re.compile(r"^([A-Za-zА-Яа-яЁё0-9_]+)/([A-Za-zА-Яа-яЁё0-9_]+)$")

# Частые «человеческие» имена → фактические поля в ЗУП OData.
FIELD_ALIASES: dict[str, str] = {
    "Подразделение": "ПодразделениеОрганизации",
    "Подразделение_Key": "ПодразделениеОрганизации_Key",
}

# Обратные алиасы, если в метаданных ИБ поле называется иначе (RecordType кадров).
REVERSE_FIELD_ALIASES: dict[str, str] = {
    "ПодразделениеОрганизации_Key": "Подразделение_Key",
    "ПодразделениеОрганизации": "Подразделение",
}

_NAV_ALIASES: dict[str, str] = {
    "Подразделение": "ПодразделениеОрганизации",
}


def _alias_candidates(name: str) -> list[str]:
    """Варианты имени поля с учётом парных алиасов подразделения."""
    stripped = name.strip()
    out: list[str] = [stripped]
    mapped = FIELD_ALIASES.get(stripped)
    if mapped and mapped not in out:
        out.append(mapped)
    reverse = REVERSE_FIELD_ALIASES.get(stripped)
    if reverse and reverse not in out:
        out.append(reverse)
    if mapped:
        rev_mapped = REVERSE_FIELD_ALIASES.get(mapped)
        if rev_mapped and rev_mapped not in out:
            out.append(rev_mapped)
    return out


def normalize_field_name(name: str, available: set[str] | frozenset[str] | None = None) -> str:
    """Подставить имя поля, существующее в метаданных сущности."""
    stripped = name.strip()
    if available is not None:
        for cand in _alias_candidates(stripped):
            if cand in available:
                return cand
        return stripped
    return stripped


def resolve_nav_property(nav_name: str, available: set[str] | frozenset[str]) -> str:
    """Выбрать имя навигационного свойства по метаданным entity."""
    stripped = nav_name.strip()
    for cand in _alias_candidates(stripped):
        if cand in available:
            return cand
    return stripped


def normalize_nav_property(
    nav_name: str,
    available: set[str] | frozenset[str] | None = None,
) -> str:
    """Нормализовать имя навигационного свойства для $expand."""
    stripped = nav_name.strip()
    if available is not None:
        return resolve_nav_property(stripped, available)
    return _NAV_ALIASES.get(stripped, stripped)


def is_virtual_table_entity(entity: str | None) -> bool:
    """True, если entity содержит виртуальную таблицу (/SliceLast(), /Balance() и т.д.)."""
    if not entity:
        return False
    return bool(_VT_SUFFIX_RE.search(entity.strip()))


def strip_virtual_table_suffix(entity: str) -> str:
    """Убрать суффикс виртуальной таблицы из entity для поиска в $metadata."""
    if not entity:
        return entity
    return _VT_SUFFIX_RE.sub("", entity.strip())


def normalize_nav_select_field(
    field: str,
    available: set[str] | frozenset[str] | None = None,
) -> str:
    """Заменить Nav/Property на Nav_Key — безопаснее для виртуальных таблиц 1С."""
    stripped = field.strip()
    match = _NAV_SELECT_RE.match(stripped)
    if not match:
        return stripped
    nav = match.group(1)
    key_name = f"{nav}_Key"
    return normalize_field_name(key_name, available)


def normalize_nav_select(
    select: str | None,
    available: set[str] | frozenset[str] | None = None,
) -> str | None:
    """Нормализовать $select: Nav/Description → Nav_Key."""
    if not select:
        return select
    prefix = ""
    raw = select
    if raw.startswith("$select="):
        prefix = "$select="
        raw = raw[len(prefix) :]
    parts = [normalize_nav_select_field(p.strip(), available) for p in raw.split(",") if p.strip()]
    if not parts:
        return None
    return prefix + ",".join(parts)


def normalize_csv_field_list(fields_csv: str | None, available: set[str] | None = None) -> str | None:
    """Нормализовать список полей в $select (через запятую)."""
    if not fields_csv:
        return fields_csv
    prefix = ""
    raw = fields_csv
    if raw.startswith("$select="):
        prefix = "$select="
        raw = raw[len(prefix) :]
    parts = [normalize_field_name(p.strip(), available) for p in raw.split(",") if p.strip()]
    if not parts:
        return None
    return prefix + ",".join(parts)


def normalize_filter_expr(filter_expr: str | None) -> str | None:
    """Заменить известные алиасы в $filter (без метаданных — только nav-пути)."""
    if not filter_expr:
        return filter_expr
    result = filter_expr
    for old, new in sorted(FIELD_ALIASES.items(), key=lambda x: -len(x[0])):
        if old.endswith("_Key"):
            continue
        result = re.sub(rf"\b{re.escape(old)}/", f"{new}/", result)
    return result


def normalize_expand(
    expand: str | None,
    available: set[str] | frozenset[str] | None = None,
) -> str | None:
    if not expand:
        return expand
    parts = [normalize_nav_property(p.strip(), available) for p in expand.split(",") if p.strip()]
    return ",".join(parts) if parts else None


def normalize_query_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Нормализовать поля query/analytics JSON от Step1."""
    if not isinstance(data, dict):
        return data

    out = dict(data)
    if out.get("filter"):
        out["filter"] = normalize_filter_expr(str(out["filter"]))
    if out.get("select"):
        out["select"] = normalize_nav_select(normalize_csv_field_list(str(out["select"])))
    if out.get("expand"):
        out["expand"] = normalize_expand(str(out["expand"]))
    if out.get("orderby"):
        out["orderby"] = normalize_csv_field_list(str(out["orderby"]))

    if out.get("mode") == "analytics":
        queries = out.get("queries")
        if isinstance(queries, list):
            normalized_queries = []
            for q in queries:
                if not isinstance(q, dict):
                    normalized_queries.append(q)
                    continue
                nq = dict(q)
                if nq.get("filter"):
                    nq["filter"] = normalize_filter_expr(str(nq["filter"]))
                if nq.get("select"):
                    nq["select"] = normalize_nav_select(normalize_csv_field_list(str(nq["select"])))
                if nq.get("expand"):
                    nq["expand"] = normalize_expand(str(nq["expand"]))
                normalized_queries.append(nq)
            out["queries"] = normalized_queries

        agg = out.get("aggregate")
        if isinstance(agg, dict):
            na = dict(agg)
            gb = na.get("group_by")
            if isinstance(gb, list):
                na["group_by"] = [normalize_field_name(str(g)) for g in gb]
            out["aggregate"] = na

        chart = out.get("chart")
        if isinstance(chart, dict):
            nc = dict(chart)
            if nc.get("x"):
                nc["x"] = normalize_field_name(str(nc["x"]))
            if nc.get("y"):
                nc["y"] = normalize_field_name(str(nc["y"]))
            out["chart"] = nc

    return out
