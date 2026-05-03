#!/usr/bin/env python3
"""Парсинг и предобработка ответов OData для AI-форматирования.

Отвечает за:
  - разрешение ссылочных полей (_Key + навигационные свойства) в представления;
  - очистку служебных полей;
  - подготовку данных для Шага 2 (AI-форматирование).
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

# Поля, которые всегда удаляются из вывода
SKIP_FIELDS: frozenset[str] = frozenset({
    "Ref_Key", "DataVersion", "DeletionMark", "Predefined",
    "PredefinedDataName", "IsFolder",
})

# Поля, которые скрываем из AI-промпта (упоминаются в STEP2_SYSTEM)
HIDDEN_FIELDS: frozenset[str] = frozenset({
    "DataVersion", "DeletionMark", "LineNumber",
    "Predefined", "PredefinedDataName", "IsFolder",
    "Ref_Key", "_Type",
})


# ---------------------------------------------------------------------------
# Разрешение ссылок
# ---------------------------------------------------------------------------

def resolve_references(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Заменить _Key-GUID на представления из раскрытых навигационных свойств.

    Для каждого поля «Имя_Key» ищет пару «Имя» (dict) и берёт из неё
    ``Description`` / ``НаименованиеПолное`` / ``Code`` / ``Ref_Key``
    как представление.  Раскрытые dict-объекты удаляются.

    Args:
        records: сырые записи из OData-ответа.

    Returns:
        Очищенные записи с человекочитаемыми представлениями вместо GUID.
    """
    resolved: list[dict[str, Any]] = []

    for rec in records:
        new_rec: dict[str, Any] = {}

        # Собираем ключи навигационных свойств (dict-значения без _Key)
        nav_keys = {
            k for k, v in rec.items()
            if isinstance(v, dict) and not k.endswith("_Key")
        }
        # Множество ключей для удаления: служебные + раскрытые объекты
        remove_keys = nav_keys | SKIP_FIELDS

        for key, value in rec.items():
            # Пропускаем служебные поля и раскрытые объекты
            if key in remove_keys:
                continue
            # Заменяем _Key на представление
            if key.endswith("_Key"):
                base = key[:-4]  # Организация_Key → Организация
                if base in rec and isinstance(rec[base], dict):
                    obj = rec[base]
                    presentation = (
                        obj.get("Description")
                        or obj.get("НаименованиеПолное")
                        or obj.get("Code")
                        or obj.get("Ref_Key")
                    )
                    if presentation and presentation != obj.get("Ref_Key"):
                        new_rec[base] = presentation
                    else:
                        # Нет представления — не показываем
                        continue
                else:
                    # Нет раскрытого объекта — не показываем GUID
                    continue
            else:
                new_rec[key] = value

        resolved.append(new_rec)

    return resolved


# ---------------------------------------------------------------------------
# Предобработка для AI
# ---------------------------------------------------------------------------

def preprocess_for_ai(
    records: list[dict[str, Any]],
    max_records: int = 30,
    max_data_length: int = 8000,
) -> str:
    """Подготовить данные для AI-форматирования (Шаг 2).

    Разрешает ссылки, обрезает до ``max_records`` записей,
    сериализует в JSON и при необходимости сокращает.

    Args:
        records: сырые записи из OData-ответа.
        max_records: максимум записей для отправки AI.
        max_data_length: максимальная длина JSON-строки.

    Returns:
        JSON-строка с предобработанными данными.
    """
    resolved = resolve_references(records)
    sample = resolved[:max_records]

    import json
    data_str = json.dumps(sample, ensure_ascii=False, indent=2)

    if len(data_str) > max_data_length:
        data_str = data_str[:max_data_length] + "\n... (данные сокращены)"

    return data_str


# ---------------------------------------------------------------------------
# Простая очистка записей
# ---------------------------------------------------------------------------

def preprocess_odata_response(
    data: dict[str, Any] | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Предобработать ответ OData: убрать служебные поля, сгруппировать.

    Принимает как полный OData-ответ ``{"value": [...]}``,
    так и отдельные записи.

    Args:
        data: сырой ответ OData (dict или list).

    Returns:
        Список записей без служебных полей.
    """
    if isinstance(data, dict):
        if "value" in data:
            records: list[dict[str, Any]] = data["value"]
        else:
            records = [data]
    elif isinstance(data, list):
        records = data
    else:
        return []

    cleaned: list[dict[str, Any]] = []
    for record in records:
        cleaned_record = {
            k: v
            for k, v in record.items()
            if k not in HIDDEN_FIELDS and not k.endswith("_Key")
        }
        cleaned.append(cleaned_record)

    return cleaned


def format_record_count(total: int, shown: int) -> str:
    """Сформировать строку с количеством записей.

    Args:
        total: общее количество записей.
        shown: количество показанных записей.

    Returns:
        Строка вида ``"Показано 20 из 100"`` или ``"Всего: 50"``.
    """
    if shown < total:
        return f"Показано {shown} из {total}"
    return f"Всего: {total}"
