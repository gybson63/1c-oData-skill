#!/usr/bin/env python3
"""Утилиты для OData-запросов: распознавание ошибок 1С, нормализация."""

from __future__ import annotations

import json
import re
from typing import Any

from bot_lib.exceptions import ODataError

_CODE6_EXTRA_SEGMENTS = re.compile(r"лишние\s+сегмент", re.I)


def is_odata_code6_extra_segments(exc: Exception) -> bool:
    """True, если 1С вернула code 6 с «лишние сегменты»."""
    if not isinstance(exc, ODataError):
        return False
    msg = exc.message or ""
    body_start = msg.find("{")
    if body_start != -1:
        try:
            body = json.loads(msg[body_start:])
            err = body.get("odata.error") or body.get("error") or body
            code = str(err.get("code", ""))
            message_value = err.get("message", "")
            if isinstance(message_value, dict):
                message_value = message_value.get("value", "")
            if code == "6" and _CODE6_EXTRA_SEGMENTS.search(str(message_value)):
                return True
        except Exception:
            pass
    return bool(_CODE6_EXTRA_SEGMENTS.search(msg))


def key_columns_from_select(select: str | None) -> list[str]:
    """Колонки *_Key из $select для post-resolve подписей."""
    if not select:
        return []
    raw = select[len("$select=") :] if select.startswith("$select=") else select
    return [p.strip() for p in raw.split(",") if p.strip().endswith("_Key")]


def slice_last_record_type_entity(entity: str | None) -> str | None:
    """Entity ``_RecordType`` для fallback, когда ``/SliceLast()`` не поддерживается OData."""
    if not entity or "/SliceLast" not in entity:
        return None
    from bot.agents.odata.field_aliases import strip_virtual_table_suffix

    base = strip_virtual_table_suffix(entity.strip())
    if not base or base.endswith("_RecordType"):
        return None
    return f"{base}_RecordType"


def dedupe_records_by_field(
    records: list[dict],
    field: str,
    *,
    limit: int | None = None,
) -> list[dict]:
    """Оставить первую запись на каждое значение ``field`` (порядок входа сохраняется)."""
    seen: set[Any] = set()
    out: list[dict] = []
    for rec in records:
        key = rec.get(field)
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
        if limit is not None and len(out) >= limit:
            break
    return out
