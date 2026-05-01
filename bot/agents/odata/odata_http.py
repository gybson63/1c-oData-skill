#!/usr/bin/env python3
"""HTTP-функции для запросов к OData 1С."""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)


class ODataError(Exception):
    """Ошибка OData-запроса."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


async def execute_odata_query(
    odata_url: str,
    auth_header: str,
    entity: str,
    filter_expr: Optional[str] = None,
    select: Optional[str] = None,
    orderby: Optional[str] = None,
    top: int = 20,
    count: bool = False,
) -> tuple[list[dict], int]:
    """Выполнить OData-запрос к 1С.

    Returns:
        Кортеж (records, total_count):
          - records: список записей (пустой при count=True)
          - total_count: количество при count=True, иначе 0
    """
    if count:
        url = f"{odata_url.rstrip('/')}/{quote(entity, safe='')}/$count"
        params: list[tuple[str, str]] = [("$format", "json")]
        if filter_expr:
            params.append(("$filter", filter_expr))
        url_str = url + "?" + "&".join(f"{k}={quote(v, safe='')}" for k, v in params)

        async with httpx.AsyncClient(verify=False, timeout=60) as client:
            resp = await client.get(url_str, headers={"Authorization": auth_header})

        if resp.status_code != 200:
            raise ODataError(f"OData /$count error: {resp.status_code} {resp.text[:500]}", resp.status_code)

        total = int(resp.text.strip())
        return [], total

    # Обычный запрос
    url = f"{odata_url.rstrip('/')}/{quote(entity, safe='')}"
    params = [("$format", "json")]
    if filter_expr:
        params.append(("$filter", filter_expr))
    if select:
        raw = select[len("$select="):] if select.startswith("$select=") else select
        params.append(("$select", raw))
    if orderby:
        raw = orderby[len("$orderby="):] if orderby.startswith("$orderby=") else orderby
        params.append(("$orderby", raw))
    if top:
        params.append(("$top", str(top)))

    url_str = url + "?" + "&".join(f"{k}={quote(v, safe='')}" for k, v in params)
    log.info("OData GET: %s", url_str)

    async with httpx.AsyncClient(verify=False, timeout=60) as client:
        resp = await client.get(url_str, headers={"Authorization": auth_header})

    if resp.status_code != 200:
        raise ODataError(f"OData error: {resp.status_code} {resp.text[:500]}", resp.status_code)

    data = resp.json()
    records = data.get("value", [])

    # Попробуем извлечь inline count
    total_str = data.get("odata.count")
    total = int(total_str) if total_str else len(records)

    return records, total