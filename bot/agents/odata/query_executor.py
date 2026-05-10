#!/usr/bin/env python3
"""Выполнение OData-запросов с fallback-стратегиями.

Инкапсулирует:
- Прямое выполнение OData-запросов через :func:`execute_odata_query`
- Fallback 1: убрать дату из фильтра если 0 записей с Number
- Fallback 2: попробовать substringof если 0 записей с Number eq
"""

from __future__ import annotations

import logging
import re

from bot.agents.odata.odata_http import execute_odata_query

log = logging.getLogger(__name__)


class QueryExecutor:
    """Выполнение OData-запросов с автоматическими fallback-стратегиями."""

    def __init__(
        self,
        odata_url: str,
        auth_header: str,
        request_timeout: int = 60,
    ) -> None:
        self._odata_url = odata_url
        self._auth_header = auth_header
        self._request_timeout = request_timeout

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
        records, total = await self._run_query(
            entity=entity,
            filter_expr=filter_expr,
            select=select,
            orderby=orderby,
            top=top,
            skip=skip,
            expand=expand,
            count=count,
        )

        if count:
            return records, total

        # Fallback 1: убрать дату из фильтра
        records, total = await self._fallback_date_filter(
            records, total, entity, filter_expr, select, orderby, top, skip, expand,
        )

        # Fallback 2: substringof
        records, total = await self._fallback_substringof(
            records, total, entity, filter_expr, select, orderby, top, skip, expand,
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
    ) -> tuple[list[dict], int]:
        """Непосредственное выполнение OData-запроса."""
        return await execute_odata_query(
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
        )

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
            "", filter_expr,
        )
        if fallback_filter == filter_expr:
            return records, total

        log.info("Fallback 1: retry without date filter: %s", fallback_filter)
        return await self._run_query(
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
        digits = re.sub(r'^[^\d]+', '', num)
        if not digits or digits == num:
            return records, total

        contains_filter = f"DeletionMark eq false and substringof('{digits}', Number)"
        log.info("Fallback 2: retry with substringof('%s', Number)", digits)
        return await self._run_query(
            entity=entity,
            filter_expr=contains_filter,
            select=select,
            orderby=orderby,
            top=top,
            skip=skip,
            expand=expand,
        )
