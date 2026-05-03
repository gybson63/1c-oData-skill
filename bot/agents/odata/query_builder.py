#!/usr/bin/env python3
"""Построение OData-запросов: $expand, URL-лимиты, приоритеты навигационных свойств.

Выделено из :mod:`bot.agents.odata.agent_1c_odata` для повторного использования
и тестируемости.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import quote

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Приоритеты раскрытия ссылочных полей через $expand
# ---------------------------------------------------------------------------

# Поля с высоким приоритетом (бизнес-суть) — раскрываются первыми.
EXPAND_HIGH_PRIORITY: tuple[str, ...] = (
    "Организация", "Контрагент", "Сотрудник", "ФизическоеЛицо",
    "Номенклатура", "Склад", "Подразделение", "Должность",
    "Валюта", "СтатьяЗатрат", "СтатьяТКРФ", "ОснованиеУвольнения",
    "Основание", "Касса", "Банк", "Проект", "НаправлениеДеятельности",
    "ВидОперации", "ХозОперация", "ВидРасчета", "ВыходноеПособие",
    "Компенсация", "Статья",
)

# Системные/подписи — раскрываются последними (могут быть отброшены при лимите).
EXPAND_LOW_PRIORITY: tuple[str, ...] = (
    "Руководитель", "ГлавныйБухгалтер", "Бухгалтер",
    "РаботникКадровойСлужбы", "Исполнитель", "Ответственный",
    "ОтветственныйИсполнитель", "Рассчитал",
    "ДолжностьРуководителя", "ДолжностьГлавногоБухгалтера",
    "ДолжностьБухгалтера", "ДолжностьРаботникаКадровойСлужбы",
    "ДолжностьИсполнителя", "ДолжностьОтветственногоИсполнителя",
    "ИсправленныйДокумент",
)


def expand_priority(nav_name: str) -> int:
    """Вернуть приоритет навигационного свойства (ниже = важнее).

    Returns:
        0 — высокий приоритет, 1 — средний, 2 — низкий, 3 — удаление.
    """
    if nav_name in EXPAND_HIGH_PRIORITY:
        return 0
    for pattern in EXPAND_HIGH_PRIORITY:
        if pattern in nav_name:
            return 0
    if nav_name in EXPAND_LOW_PRIORITY:
        return 2
    for pattern in EXPAND_LOW_PRIORITY:
        if pattern in nav_name:
            return 2
    if nav_name.startswith("Удалить") or nav_name.startswith("Delete"):
        return 3
    return 1  # средний приоритет


# ---------------------------------------------------------------------------
# Построение $expand
# ---------------------------------------------------------------------------

# Поля _Key, которые не являются навигационными свойствами
_SKIP_KEY_FIELDS = (
    "Ref_Key", "DataVersion", "Predefined", "PredefinedDataName",
    "IsFolder", "LineNumber", "Parent_Key",
)


def build_expand(
    entity: str,
    select: Optional[str],
    entity_fields: list[str],
    max_expand_fields: int = 15,
) -> Optional[str]:
    """Построить ``$expand`` на основе _Key полей.

    Если ``select`` задан — извлекает _Key-поля из него.
    Если ``select is None`` — использует ``entity_fields`` из метаданных.

    Сортирует навигационные свойства по приоритету и ограничивает
    их количество до ``max_expand_fields``.

    Args:
        entity: имя сущности (для логирования).
        select: значение ``$select`` или ``None``.
        entity_fields: список полей сущности из метаданных.
        max_expand_fields: максимальное количество expand-свойств.

    Returns:
        Строка ``$expand`` или ``None``.
    """
    nav_names: list[str] = []

    if select:
        raw = select[len("$select="):] if select.startswith("$select=") else select
        field_names = [f.strip() for f in raw.split(",") if f.strip()]
    else:
        field_names = entity_fields

    for f in field_names:
        if f.endswith("_Key") and f not in _SKIP_KEY_FIELDS:
            nav_name = f[:-4]  # убрать суффикс _Key
            nav_names.append(nav_name)

    if not nav_names:
        return None

    # Сортировать по приоритету
    nav_names.sort(key=expand_priority)

    # Ограничить количество
    if len(nav_names) > max_expand_fields:
        log.info(
            "$expand для %s: %d свойств → ограничено до %d (с приоритетом)",
            entity, len(nav_names), max_expand_fields,
        )
        nav_names = nav_names[:max_expand_fields]

    expand = ",".join(nav_names)
    log.info("Auto $expand for %s: %s (from fields: %s)", entity, expand, field_names[:20])
    return expand


# ---------------------------------------------------------------------------
# Оценка и обрезка URL
# ---------------------------------------------------------------------------

def estimate_url_length(
    odata_url: str,
    entity: str,
    filter_expr: Optional[str],
    select: Optional[str],
    orderby: Optional[str],
    top: int,
    expand: Optional[str],
) -> int:
    """Приблизительная оценка длины итогового URL OData-запроса."""
    base = f"{odata_url.rstrip('/')}/{quote(entity, safe='')}"
    params: list[tuple[str, str]] = [("$format", "json")]
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
    if expand:
        raw_expand = expand[len("$expand="):] if expand.startswith("$expand=") else expand
        params.append(("$expand", raw_expand))
    url_str = base + "?" + "&".join(f"{k}={quote(v, safe='')}" for k, v in params)
    return len(url_str)


def trim_expand_for_url_limit(
    odata_url: str,
    entity: str,
    filter_expr: Optional[str],
    select: Optional[str],
    orderby: Optional[str],
    top: int,
    expand: Optional[str],
    max_url_length: int = 1800,
) -> Optional[str]:
    """Обрезать ``$expand``, если итоговый URL превышает ``max_url_length``.

    Удаляет свойства с конца (с низким приоритетом), пока URL не уложится
    в лимит.  Если даже одно свойство не помогает — возвращает ``None``.
    """
    if not expand:
        return expand

    url_len = estimate_url_length(
        odata_url, entity, filter_expr, select, orderby, top, expand,
    )
    if url_len <= max_url_length:
        return expand

    original_len = len(expand.split(","))
    nav_list = expand.split(",")

    while len(nav_list) > 1:
        nav_list = nav_list[:-1]
        trimmed = ",".join(nav_list)
        url_len = estimate_url_length(
            odata_url, entity, filter_expr, select, orderby, top, trimmed,
        )
        if url_len <= max_url_length:
            log.info(
                "$expand сокращён: %d → %d свойств (URL %d → %d)",
                original_len, len(nav_list),
                estimate_url_length(
                    odata_url, entity, filter_expr, select, orderby, top, expand,
                ),
                url_len,
            )
            return trimmed

    log.warning("$expand убран полностью — URL слишком длинный (%d)", url_len)
    return None