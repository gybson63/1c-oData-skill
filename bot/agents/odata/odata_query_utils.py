#!/usr/bin/env python3
"""Утилиты для OData-запросов: распознавание ошибок 1С, нормализация."""

from __future__ import annotations

import json
import re
from typing import Any

from bot_lib.exceptions import ODataError

_CODE6_EXTRA_SEGMENTS = re.compile(r"лишние\s+сегмент", re.I)
_CODE6_METHOD_NOT_FOUND = re.compile(r"метод\s+не\s+найден", re.I)
_CODE6_SEGMENT_NOT_FOUND = re.compile(r"Сегмент пути\s+(\S+)\s+не найден", re.I)


def _parse_odata_error_message(exc: Exception) -> tuple[str, str]:
    """Вернуть (code, message_value) из ODataError."""
    if not isinstance(exc, ODataError):
        return "", str(exc)
    msg = exc.message or ""
    body_start = msg.find("{")
    if body_start == -1:
        return "", msg
    try:
        body = json.loads(msg[body_start:])
        err = body.get("odata.error") or body.get("error") or body
        code = str(err.get("code", ""))
        message_value = err.get("message", "")
        if isinstance(message_value, dict):
            message_value = message_value.get("value", "")
        return code, str(message_value)
    except Exception:
        return "", msg


def is_odata_code6_method_not_found(exc: Exception) -> bool:
    """True, если 1С вернула code 6 «Метод не найден» (VT не опубликована)."""
    code, message_value = _parse_odata_error_message(exc)
    if code == "6" and _CODE6_METHOD_NOT_FOUND.search(message_value):
        return True
    return bool(_CODE6_METHOD_NOT_FOUND.search(str(exc)))


def is_odata_code6_segment_not_found(exc: Exception) -> bool:
    """True, если code 6 — сегмент пути (поле/VT) не найден."""
    code, message_value = _parse_odata_error_message(exc)
    if code == "6" and _CODE6_SEGMENT_NOT_FOUND.search(message_value):
        return True
    return bool(_CODE6_SEGMENT_NOT_FOUND.search(str(exc)))


def is_odata_code6_extra_segments(exc: Exception) -> bool:
    """True, если 1С вернула code 6 с «лишние сегменты»."""
    if not isinstance(exc, ODataError):
        return False
    code, message_value = _parse_odata_error_message(exc)
    if code == "6" and _CODE6_EXTRA_SEGMENTS.search(message_value):
        return True
    msg = exc.message or ""
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


def information_register_record_type_entity(entity: str | None) -> str | None:
    """``_RecordType`` для IR-шапки, когда поля среза недоступны на header entity."""
    if not entity:
        return None
    from bot.agents.odata.field_aliases import is_virtual_table_entity

    base = entity.strip().split("?")[0]
    if not base.startswith("InformationRegister_"):
        return None
    if base.endswith("_RecordType") or is_virtual_table_entity(base):
        return None
    return f"{base}_RecordType"


def next_accumulation_vt_entity(entity: str | None, exc: Exception) -> str | None:
    """Следующая VT для AccumulationRegister при «Метод не найден» (/Balance → /Turnovers)."""
    if not entity or not is_odata_code6_method_not_found(exc):
        return None
    from bot.agents.odata.field_aliases import strip_virtual_table_suffix

    stripped = entity.strip()
    base = strip_virtual_table_suffix(stripped)
    if not base.startswith("AccumulationRegister_"):
        return None
    if "/Balance()" in stripped and "/Turnovers()" not in stripped:
        return f"{base}/Turnovers()"
    if "/Balance()" in stripped and "/BalanceAndTurnovers()" not in stripped:
        return f"{base}/BalanceAndTurnovers()"
    return None


def record_type_orderby(fields: list[str]) -> str:
    """Поле сортировки для fallback через ``_RecordType``."""
    field_set = set(fields)
    for candidate in ("Period", "Дата", "ДатаОстатка"):
        if candidate in field_set:
            return f"{candidate} desc"
    return "Period desc"


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
