#!/usr/bin/env python3
"""HTTP-функции для запросов к OData 1С.

Делегирует реальную HTTP-работу :class:`lib.odata_client.ODataClient`,
сохраняя обратную совместимость с остальным кодом бота.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import quote

from bot_lib.exceptions import ODataError
from bot_lib.odata_client import ODataClient

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Обратно-совместимая функция, используемая agent_1c_odata.py
# ---------------------------------------------------------------------------

async def execute_odata_query(
    odata_url: str,
    auth_header: str,
    entity: str,
    filter_expr: Optional[str] = None,
    select: Optional[str] = None,
    orderby: Optional[str] = None,
    top: int = 20,
    count: bool = False,
    expand: Optional[str] = None,
    request_timeout: int = 60,
    max_url_length: int = 2000,
) -> tuple[list[dict], int]:
    """Выполнить OData-запрос к 1С.

    Создаёт временный :class:`ODataClient` на каждый вызов
    (обратная совместимость с текущим кодом агента).

    Args:
        odata_url: базовый URL OData
        auth_header: заголовок ``Authorization`` (``Basic ...``)
        entity: имя набора сущностей
        filter_expr: OData ``$filter``
        select: OData ``$select`` (значение или ``$select=...``)
        orderby: OData ``$orderby`` (значение или ``$orderby=...``)
        top: OData ``$top``
        count: если ``True`` — запрос ``/$count``
        expand: OData ``$expand``
        request_timeout: таймаут HTTP в секундах
        max_url_length: максимальная длина URL

    Returns:
        Кортеж ``(records, total_count)``.
    """
    # Нормализуем параметры — убираем префиксы "$select=" / "$orderby="
    clean_select = select
    if clean_select and clean_select.startswith("$select="):
        clean_select = clean_select[len("$select="):]

    clean_orderby = orderby
    if clean_orderby and clean_orderby.startswith("$orderby="):
        clean_orderby = clean_orderby[len("$orderby="):]

    clean_expand = expand
    if clean_expand and clean_expand.startswith("$expand="):
        clean_expand = clean_expand[len("$expand="):]

    try:
        async with ODataClient(
            base_url=odata_url,
            auth_header=auth_header,
            timeout=request_timeout,
            verify_ssl=False,
            max_url_length=max_url_length,
        ) as client:
            if count:
                total = await client.get_count(entity, filter_=filter_expr)
                return [], total

            data = await client.get_entities(
                entity=entity,
                filter_=filter_expr,
                select=clean_select,
                orderby=clean_orderby,
                top=top,
                expand=clean_expand,
            )

            records = data.get("value", [])

            # Извлечь inline count
            total_str = data.get("odata.count") or data.get("@odata.count")
            total = int(total_str) if total_str else len(records)

            log.info(
                "OData response: records=%d, total=%s",
                len(records), total,
            )
            if records:
                log.info("OData first record keys: %s", list(records[0].keys())[:10])
            else:
                log.warning("OData вернул 0 записей")

            return records, total

    except ODataError:
        raise
    except Exception as exc:
        raise ODataError(f"Ошибка OData-запроса: {exc}") from exc