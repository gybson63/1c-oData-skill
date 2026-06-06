#!/usr/bin/env python3
"""Разбор JSON-ответа AI Step 1 (query и analytics)."""

from __future__ import annotations

import logging
from typing import Any

from bot.agents.odata.analytics_models import AnalyticsPlan
from bot.agents.odata.field_aliases import normalize_query_dict
from bot.agents.odata.parse_failure import ParseFailureReason
from bot.agents.odata.state import ODataQuery, ODataState

log = logging.getLogger(__name__)

_INLINE_TOOL_NAMES = frozenset({"odata_reference", "get_entity_fields", "search_entities"})

# Последняя причина неудачного apply_step1_dict (для classify/journal).
_last_apply_failure: str | None = None


def get_last_apply_failure() -> str | None:
    return _last_apply_failure


def is_inline_tool_call(parsed: dict[str, Any]) -> bool:
    """Проверить, выглядит ли JSON как встроенный вызов инструмента."""
    if not isinstance(parsed, dict) or "entity" in parsed or parsed.get("mode") == "analytics":
        return False
    tool_name = parsed.get("name") or parsed.get("function")
    return tool_name in _INLINE_TOOL_NAMES and isinstance(parsed.get("arguments"), dict)


def apply_step1_dict(state: ODataState, query_dict: dict[str, Any] | None) -> bool:
    """Применить распарсенный JSON к state. Возвращает True, если режим распознан."""
    global _last_apply_failure
    _last_apply_failure = None

    if not query_dict or not isinstance(query_dict, dict):
        return False

    query_dict = normalize_query_dict(query_dict)

    if query_dict.get("mode") == "analytics":
        try:
            state.analytics_plan = AnalyticsPlan.from_dict(query_dict)
            return True
        except (ValueError, TypeError) as exc:
            _last_apply_failure = ParseFailureReason.ANALYTICS_INVALID
            log.warning("Analytics plan validation failed: %s", exc)
            return False

    if query_dict.get("entity") and not is_inline_tool_call(query_dict):
        state.query = ODataQuery.from_dict(query_dict)
        return True

    return False
