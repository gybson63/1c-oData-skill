#!/usr/bin/env python3
"""Выполнение OData-запросов с fallback-стратегиями.

Инкапсулирует:
- Прямое выполнение OData-запросов через :func:`execute_odata_query`
- Fallback: code 6 «лишние сегменты» — без $expand, без $inlinecount
- Fallback: убрать дату из фильтра если 0 записей с Number
- Fallback: substringof если 0 записей с Number eq
- Post-resolve _Key → Description для виртуальных таблиц без $expand
"""

from __future__ import annotations

import logging
import re
from typing import Any

from bot.agents.odata.field_aliases import normalize_field_name, normalize_nav_select
from bot.agents.odata.odata_http import execute_odata_query
from bot.agents.odata.odata_query_utils import (
    dedupe_records_by_field,
    is_odata_code6_extra_segments,
    key_columns_from_select,
    slice_last_record_type_entity,
)
from bot_lib.exceptions import ODataError

log = logging.getLogger(__name__)


class QueryExecutor:
    """Выполнение OData-запросов с автоматическими fallback-стратегиями."""

    def __init__(
        self,
        odata_url: str,
        auth_header: str,
        request_timeout: int = 60,
        metadata: Any | None = None,
    ) -> None:
        self._odata_url = odata_url
        self._auth_header = auth_header
        self._request_timeout = request_timeout
        self._metadata = metadata

    async def execute(
        self,
        entity: str,
        filter_expr: str | None = None,
        select: str | None = None,
        orderby: str | None = None,
        top: int = 20,
        skip: int | None = None,
        expand: str | None = None,
        count: bool = False,
    ) -> tuple[list[dict], int]:
        """Выполнить OData-запрос с применением fallback-стратегий.

        Returns:
            Кортеж (records, total).
        """
        select = normalize_nav_select(select)

        records, total = await self._run_query_with_code6_fallback(
            entity=entity,
            filter_expr=filter_expr,
            select=select,
            orderby=orderby,
            top=top,
            skip=skip,
            expand=expand,
            count=count,
        )

        if not count and records:
            records = await self._maybe_resolve_key_labels(records, select)

        if count:
            return records, total

        # Fallback 1: убрать дату из фильтра
        records, total = await self._fallback_date_filter(
            records,
            total,
            entity,
            filter_expr,
            select,
            orderby,
            top,
            skip,
            expand,
        )

        # Fallback 2: substringof
        records, total = await self._fallback_substringof(
            records,
            total,
            entity,
            filter_expr,
            select,
            orderby,
            top,
            skip,
            expand,
        )

        return records, total

    async def execute_count(
        self,
        entity: str,
        filter_expr: str | None = None,
    ) -> tuple[list[dict], int]:
        """Выполнить запрос на подсчёт записей ($count=true)."""
        return await self._run_query(
            entity=entity,
            filter_expr=filter_expr,
            count=True,
        )

    async def _run_query_with_code6_fallback(
        self,
        entity: str,
        filter_expr: str | None = None,
        select: str | None = None,
        orderby: str | None = None,
        top: int = 20,
        skip: int | None = None,
        expand: str | None = None,
        count: bool = False,
    ) -> tuple[list[dict], int]:
        """Выполнить запрос; при code 6 «лишние сегменты» упростить параметры."""
        try:
            return await self._run_query(
                entity=entity,
                filter_expr=filter_expr,
                select=select,
                orderby=orderby,
                top=top,
                skip=skip,
                expand=expand,
                count=count,
                inline_count=True,
            )
        except ODataError as exc:
            if count or not is_odata_code6_extra_segments(exc):
                raise
            first_exc = exc

        if expand:
            log.info("OData code 6 fallback: retry without $expand for %s", entity)
            try:
                return await self._run_query(
                    entity=entity,
                    filter_expr=filter_expr,
                    select=select,
                    orderby=orderby,
                    top=top,
                    skip=skip,
                    expand=None,
                    count=count,
                    inline_count=True,
                    resolve_keys=True,
                )
            except ODataError as exc2:
                if not is_odata_code6_extra_segments(exc2):
                    raise
                first_exc = exc2

        log.info("OData code 6 fallback: retry without $inlinecount for %s", entity)
        try:
            return await self._run_query(
                entity=entity,
                filter_expr=filter_expr,
                select=select,
                orderby=orderby,
                top=top,
                skip=skip,
                expand=None,
                count=count,
                inline_count=False,
                resolve_keys=True,
            )
        except ODataError as exc3:
            rt_entity = slice_last_record_type_entity(entity)
            if rt_entity and is_odata_code6_extra_segments(exc3):
                log.info(
                    "OData code 6 fallback: SliceLast unsupported, use %s with Period desc",
                    rt_entity,
                )
                return await self._run_slice_last_via_record_type(
                    rt_entity=rt_entity,
                    filter_expr=filter_expr,
                    select=select,
                    top=top,
                    skip=skip,
                )
            raise first_exc

    async def _run_query(
        self,
        entity: str,
        filter_expr: str | None = None,
        select: str | None = None,
        orderby: str | None = None,
        top: int = 20,
        skip: int | None = None,
        expand: str | None = None,
        count: bool = False,
        *,
        inline_count: bool = True,
        resolve_keys: bool = False,
    ) -> tuple[list[dict], int]:
        """Непосредственное выполнение OData-запроса."""
        records, total = await execute_odata_query(
            odata_url=self._odata_url,
            auth_header=self._auth_header,
            entity=entity,
            filter_expr=filter_expr,
            select=select,
            orderby=orderby,
            top=top,
            skip=skip,
            expand=expand,
            count=count,
            request_timeout=self._request_timeout,
            inline_count=inline_count,
        )
        if resolve_keys and records and not expand:
            records = await self._resolve_key_labels(records, select)
        return records, total

    async def _run_slice_last_via_record_type(
        self,
        rt_entity: str,
        filter_expr: str | None,
        select: str | None,
        top: int,
        skip: int | None,
    ) -> tuple[list[dict], int]:
        """Срез последних через ``_RecordType`` + ``$orderby=Period desc`` и dedupe по сотруднику."""
        fields: list[str] = []
        if self._metadata is not None:
            try:
                fields = list(self._metadata.get_entity_fields(rt_entity))
            except Exception:
                fields = []

        adapted_select = select
        if fields and select:
            field_set = set(fields)
            parts: list[str] = []
            seen: set[str] = set()
            for p in select.split(","):
                p = p.strip()
                if not p:
                    continue
                resolved = normalize_field_name(p, field_set)
                if resolved in field_set and resolved not in seen:
                    parts.append(resolved)
                    seen.add(resolved)
            adapted_select = ",".join(parts) if parts else select

        adapted_filter = filter_expr
        if fields and filter_expr and "DeletionMark" in filter_expr and "DeletionMark" not in fields:
            adapted_filter = re.sub(
                r"DeletionMark\s+eq\s+false\s+and\s+",
                "",
                filter_expr,
                flags=re.I,
            )
            adapted_filter = (
                re.sub(
                    r"\s*and\s+DeletionMark\s+eq\s+false",
                    "",
                    adapted_filter or "",
                    flags=re.I,
                ).strip()
                or None
            )

        fetch_top = min(max(top * 30, 50), 500)
        records, _total = await self._run_query(
            entity=rt_entity,
            filter_expr=adapted_filter,
            select=adapted_select,
            orderby="Period desc",
            top=fetch_top,
            skip=skip,
            expand=None,
            inline_count=False,
            resolve_keys=True,
        )

        dedupe_key = "Сотрудник_Key"
        if adapted_select:
            key_cols = key_columns_from_select(adapted_select)
            if key_cols:
                dedupe_key = key_cols[0]

        records = dedupe_records_by_field(records, dedupe_key, limit=top)
        return records, len(records)

    async def _maybe_resolve_key_labels(
        self,
        records: list[dict],
        select: str | None,
    ) -> list[dict]:
        """Подставить подписи для *_Key, если $expand не раскрыл навигацию."""
        key_cols = key_columns_from_select(select)
        if not key_cols or not records:
            return records
        first = records[0]
        for col in key_cols:
            nav = col[:-4] if col.endswith("_Key") else col
            if nav in first and isinstance(first.get(nav), dict):
                return records
        return await self._resolve_key_labels(records, select)

    async def _resolve_key_labels(
        self,
        records: list[dict],
        select: str | None,
    ) -> list[dict]:
        """Подставить Description вместо GUID для *_Key после запроса без $expand."""
        key_cols = key_columns_from_select(select)
        if not key_cols:
            return records
        try:
            from bot.agents.odata.analytics_reference_labels import resolve_reference_labels_in_records

            return await resolve_reference_labels_in_records(
                records,
                key_cols,
                self,
                self._metadata,
            )
        except Exception as exc:
            log.warning("Post-resolve reference labels failed: %s", exc)
            return records

    async def _fallback_date_filter(
        self,
        records: list[dict],
        total: int,
        entity: str,
        filter_expr: str | None,
        select: str | None,
        orderby: str | None,
        top: int,
        skip: int | None,
        expand: str | None,
    ) -> tuple[list[dict], int]:
        """Fallback 1: убрать datetime-условия если 0 записей и фильтр содержит Number."""
        if total != 0 or not filter_expr or "Number" not in filter_expr:
            return records, total

        fallback_filter = re.sub(
            r"\s*and\s+\w+\s+(eq|ge|le|gt|lt)\s+datetime'[^']*'",
            "",
            filter_expr,
        )
        if fallback_filter == filter_expr:
            return records, total

        log.info("Fallback 1: retry without date filter: %s", fallback_filter)
        return await self._run_query_with_code6_fallback(
            entity=entity,
            filter_expr=fallback_filter,
            select=select,
            orderby=orderby,
            top=top,
            skip=skip,
            expand=expand,
        )

    async def _fallback_substringof(
        self,
        records: list[dict],
        total: int,
        entity: str,
        filter_expr: str | None,
        select: str | None,
        orderby: str | None,
        top: int,
        skip: int | None,
        expand: str | None,
    ) -> tuple[list[dict], int]:
        """Fallback 2: попробовать substringof если 0 записей с Number eq."""
        if total != 0 or not filter_expr or "Number eq '" not in filter_expr:
            return records, total

        number_match = re.search(r"Number eq '([^']*)'", filter_expr)
        if not number_match:
            return records, total

        num = number_match.group(1)
        digits = re.sub(r"^[^\d]+", "", num)
        if not digits or digits == num:
            return records, total

        contains_filter = f"DeletionMark eq false and substringof('{digits}', Number)"
        log.info("Fallback 2: retry with substringof('%s', Number)", digits)
        return await self._run_query_with_code6_fallback(
            entity=entity,
            filter_expr=contains_filter,
            select=select,
            orderby=orderby,
            top=top,
            skip=skip,
            expand=expand,
        )
