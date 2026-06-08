#!/usr/bin/env python3
"""Валидация и корректировка OData-запросов по метаданным.

Проверяет $select, $orderby, строит $expand и контролирует
длину URL, чтобы не превышать лимиты.
"""

from __future__ import annotations

import logging
from typing import Any

from bot.agents.odata.field_aliases import (
    is_virtual_table_entity,
    normalize_expand,
    normalize_field_name,
    normalize_nav_select,
)
from bot.agents.odata.query_builder import build_expand, trim_expand_for_url_limit

log = logging.getLogger(__name__)


class QueryValidator:
    """Валидация OData-запроса по метаданным сущности."""

    def __init__(
        self,
        metadata: Any,
        odata_url: str,
        default_top: int = 20,
        max_top: int = 50,
        max_expand_fields: int = 15,
        max_url_length: int = 1800,
    ) -> None:
        self._metadata = metadata
        self._odata_url = odata_url
        self._default_top = default_top
        self._max_top = max_top
        self._max_expand_fields = max_expand_fields
        self._max_url_length = max_url_length

    def validate(self, query: Any) -> dict[str, Any]:
        """Валидировать и скорректировать запрос.

        Args:
            query: :class:`ODataQuery` для валидации.

        Returns:
            Словарь с валидированными параметрами:
            ``select``, ``orderby``, ``expand``, ``top``, ``skip``.
        """
        top = min(int(query.top) or self._default_top, self._max_top)
        skip = query.skip

        select = self._normalize_list(query.select)
        orderby = self._normalize_list(query.orderby)

        fields = self._metadata.get_entity_fields(query.entity)
        field_set = frozenset(fields) if fields else frozenset()
        if fields:
            log.info("Fields for %s: %s", query.entity, fields)
            select = normalize_nav_select(select, field_set)
            select = self._validate_select(fields, select)
            orderby = self._validate_orderby(fields, orderby)
        elif select:
            select = normalize_nav_select(select)

        # $expand на виртуальных таблицах (SliceLast и т.д.) часто отклоняется 1С
        if is_virtual_table_entity(query.entity):
            expand = None
            log.info("$expand omitted for virtual table entity: %s", query.entity)
        elif query.expand:
            expand = normalize_expand(str(query.expand), field_set or None)
            if field_set and expand:
                valid = [p for p in expand.split(",") if p.strip() in field_set]
                expand = ",".join(valid) if valid else None
        else:
            expand = build_expand(query.entity, select, fields, self._max_expand_fields)

        if expand is not None and not is_virtual_table_entity(query.entity):
            expand = trim_expand_for_url_limit(
                self._odata_url,
                query.entity,
                query.filter_expr,
                select,
                orderby,
                top,
                expand,
                max_url_length=self._max_url_length,
            )

        return {
            "select": select,
            "orderby": orderby,
            "expand": expand,
            "top": top,
            "skip": skip,
        }

    @staticmethod
    def _normalize_list(value: str | list | None) -> str | None:
        """Преобразовать list или строку с $-префиксом в простую строку."""
        if isinstance(value, list):
            return ",".join(str(s) for s in value)
        return value

    @staticmethod
    def _validate_select(fields: list[str], select: str | None) -> str | None:
        """Скорректировать $select, оставив только существующие поля."""
        if not select:
            return select
        raw_select = select[len("$select=") :] if select.startswith("$select=") else select
        field_set = set(fields)
        valid: list[str] = []
        seen: set[str] = set()
        for part in raw_select.split(","):
            part = part.strip()
            if not part:
                continue
            resolved = normalize_field_name(part, field_set)
            if resolved in fields and resolved not in seen:
                valid.append(resolved)
                seen.add(resolved)
        result = ",".join(valid) if valid else None
        if result != raw_select:
            log.info("$select скорректирован: %s → %s", raw_select, result)
        return result

    @staticmethod
    def _validate_orderby(fields: list[str], orderby: str | None) -> str | None:
        """Скорректировать $orderby, проверив что поле существует."""
        if not orderby:
            return orderby
        raw_orderby = orderby[len("$orderby=") :] if orderby.startswith("$orderby=") else orderby
        field_name = raw_orderby.split()[0]
        resolved = normalize_field_name(field_name, set(fields))
        if resolved not in fields:
            log.info("$orderby '%s' не найден в полях, убираем", field_name)
            return None
        return orderby
