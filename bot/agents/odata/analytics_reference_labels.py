#!/usr/bin/env python3
"""Подстановка человекочитаемых представлений вместо GUID в analytics DataFrame."""

from __future__ import annotations

import logging
import re
from typing import Any

import pandas as pd

from bot.agents.odata.query_executor import QueryExecutor

log = logging.getLogger(__name__)

_GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_EMPTY_GUID = "00000000-0000-0000-0000-000000000000"

NAV_PROPERTY_TO_CATALOG: dict[str, str] = {
    "ГоловнаяОрганизация": "Catalog_Организации",
    "Организация": "Catalog_Организации",
    "Контрагент": "Catalog_Контрагенты",
    "Сотрудник": "Catalog_Сотрудники",
    "ФизическоеЛицо": "Catalog_ФизическиеЛица",
    "Номенклатура": "Catalog_Номенклатура",
    "Склад": "Catalog_Склады",
    "Подразделение": "Catalog_ПодразделенияОрганизаций",
    "ПодразделениеОрганизации": "Catalog_ПодразделенияОрганизаций",
    "Должность": "Catalog_Должности",
}


def is_guid(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return bool(_GUID_RE.match(text))


_PRESENTATION_FIELD_CANDIDATES = ("Description", "НаименованиеПолное", "Code", "Number")


def build_presentation_select(catalog_entity: str, metadata: Any | None = None) -> str:
    """Собрать $select для lookup представлений — только поля, существующие в сущности."""
    parts = ["Ref_Key"]
    if metadata is not None:
        try:
            entity_fields = set(metadata.get_entity_fields(catalog_entity))
        except Exception:
            entity_fields = set()
        if entity_fields:
            for name in _PRESENTATION_FIELD_CANDIDATES:
                if name in entity_fields:
                    parts.append(name)
            return ",".join(parts)
    parts.extend(("Description", "НаименованиеПолное"))
    return ",".join(parts)


def presentation_select_fallbacks(catalog_entity: str, metadata: Any | None = None) -> list[str | None]:
    """Варианты $select от узкого к широкому (последний — все поля)."""
    primary = build_presentation_select(catalog_entity, metadata)
    fallbacks: list[str | None] = [primary]
    minimal = "Ref_Key,Description"
    if primary != minimal:
        fallbacks.append(minimal)
    if None not in fallbacks:
        fallbacks.append(None)
    return fallbacks


def presentation_from_record(record: dict[str, Any]) -> str | None:
    for field in ("Description", "НаименованиеПолное", "Code", "Number"):
        value = record.get(field)
        if value and str(value).strip():
            return str(value).strip()
    return None


def guess_catalog_entity(column: str, metadata: Any | None = None) -> str | None:
    """Определить OData-сущность справочника для колонки-ссылки."""
    if column in NAV_PROPERTY_TO_CATALOG:
        return NAV_PROPERTY_TO_CATALOG[column]

    for nav, catalog in NAV_PROPERTY_TO_CATALOG.items():
        if nav in column or column in nav:
            return catalog

    if metadata is not None:
        candidates = metadata.search_entities(column.replace("_Key", ""))
        for name in candidates:
            if name.startswith("Catalog_"):
                return str(name)

    return None


async def resolve_reference_labels_in_dataframe(
    df: pd.DataFrame,
    columns: list[str],
    executor: QueryExecutor,
    metadata: Any | None = None,
) -> pd.DataFrame:
    """Заменить GUID в указанных колонках на Description/Code из связанного справочника."""
    if df.empty or not columns:
        return df

    result = df.copy()
    for column in columns:
        if column not in result.columns:
            continue
        guids = sorted({str(v).strip() for v in result[column].dropna().tolist() if is_guid(v)})
        if not guids:
            continue

        catalog = guess_catalog_entity(column, metadata)
        if not catalog:
            log.warning("Не удалось определить справочник для колонки %s", column)
            continue

        labels = await _fetch_ref_labels(executor, catalog, guids, metadata)
        if not labels:
            continue

        result[column] = result[column].map(
            lambda value: _map_label(value, labels),
        )
    return result


def _map_label(value: Any, labels: dict[str, str]) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return value
    text = str(value).strip()
    if text == _EMPTY_GUID:
        return "Не указано"
    if is_guid(text):
        return labels.get(text, text)
    return value


async def _fetch_ref_labels(
    executor: QueryExecutor,
    catalog_entity: str,
    guids: list[str],
    metadata: Any | None = None,
) -> dict[str, str]:
    labels: dict[str, str] = {}
    chunk_size = 15
    select_variants = presentation_select_fallbacks(catalog_entity, metadata)

    for i in range(0, len(guids), chunk_size):
        chunk = guids[i : i + chunk_size]
        filter_expr = " or ".join(f"Ref_Key eq guid'{guid}'" for guid in chunk)
        records: list[dict[str, Any]] = []
        last_error: Exception | None = None

        for select in select_variants:
            try:
                records, _ = await executor.execute(
                    entity=catalog_entity,
                    filter_expr=filter_expr,
                    select=select,
                    top=len(chunk),
                )
                last_error = None
                break
            except Exception as e:
                last_error = e
                log.debug(
                    "Label lookup %s select=%r failed: %s",
                    catalog_entity,
                    select,
                    e,
                )

        if last_error is not None:
            log.warning(
                "Не удалось загрузить представления из %s: %s",
                catalog_entity,
                last_error,
            )
            continue

        for record in records:
            ref_key = record.get("Ref_Key")
            if not ref_key:
                continue
            label = presentation_from_record(record)
            if label:
                labels[str(ref_key)] = label

    log.info(
        "Resolved %d/%d GUID labels via %s",
        len(labels),
        len(guids),
        catalog_entity,
    )
    return labels


async def resolve_reference_labels_in_records(
    records: list[dict[str, Any]],
    key_columns: list[str],
    executor: QueryExecutor,
    metadata: Any | None = None,
) -> list[dict[str, Any]]:
    """Заменить GUID в *_Key колонках на Description/Code (без $expand)."""
    if not records or not key_columns:
        return records

    out: list[dict[str, Any]] = [dict(r) for r in records]
    for column in key_columns:
        if not any(column in r for r in out):
            continue
        guids = sorted(
            {str(r[column]).strip() for r in out if column in r and is_guid(r[column])},
        )
        if not guids:
            continue

        nav_name = column[:-4] if column.endswith("_Key") else column
        catalog = guess_catalog_entity(nav_name, metadata)
        if not catalog:
            log.warning("Не удалось определить справочник для %s", column)
            continue

        labels = await _fetch_ref_labels(executor, catalog, guids, metadata)
        if not labels:
            continue

        for row in out:
            if column in row:
                row[column] = _map_label(row[column], labels)

    return out
