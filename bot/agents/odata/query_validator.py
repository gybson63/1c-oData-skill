#!/usr/bin/env python3
"""Валидация и корректировка OData-запросов по метаданным.

Проверяет $select, $orderby, строит $expand и контролирует
длину URL, чтобы не превышать лимиты.
"""

from __future__ import annotations

import logging
from typing import Any

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

        # Валидация $select
        select = self._normalize_list(query.select)
        orderby = self._normalize_list(query.orderby)

        fields = self._metadata.get_entity_fields(query.entity)
        if fields:
            log.info("Fields for %s: %s", query.entity, fields)
            select = self._validate_select(fields, select)
            orderby = self._validate_orderby(fields, orderby)

        # Построить $expand
        expand = build_expand(query.entity, select, fields, self._max_expand_fields)

        # Проверить длину URL
        expand = trim_expand_for_url_limit(
            self._odata_url, query.entity, query.filter_expr,
            select, orderby, top, expand,
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
        raw_select = select[len("$select="):] if select.startswith("$select=") else select
        valid = [f.strip() for f in raw_select.split(",") if f.strip() in fields]
        result = ",".join(valid) if valid else None
        if result != raw_select:
            log.info("$select скорректирован: %s → %s", raw_select, result)
        return result

    @staticmethod
    def _validate_orderby(fields: list[str], orderby: str | None) -> str | None:
        """Скорректировать $orderby, проверив что поле существует."""
        if not orderby:
            return orderby
        raw_orderby = orderby[len("$orderby="):] if orderby.startswith("$orderby=") else orderby
        field_name = raw_orderby.split()[0]
        if field_name not in fields:
            log.info("$orderby '%s' не найден в полях, убираем", field_name)
            return None
        return orderby
