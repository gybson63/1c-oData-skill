#!/usr/bin/env python3
"""Работа с $metadata 1С OData.

Загрузка, кэширование и поиск сущностей / полей.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

import httpx

log = logging.getLogger(__name__)

METADATA_FILE = "metadata_cache.json"
METADATA_CACHE_SECONDS = 86400  # 24 часа


# ---------------------------------------------------------------------------
# Загрузка $metadata
# ---------------------------------------------------------------------------

def _iter_entity_types(root: ET.Element):
    """Итератор по всем EntityType из $metadata (поддержка разных namespace)."""
    # Known EDM namespaces used by 1C OData
    edm_namespaces = [
        "http://schemas.microsoft.com/ado/2008/09/edm",
        "http://schemas.microsoft.com/ado/2009/11/edm",
    ]
    for ns in edm_namespaces:
        ns_tag = f"{{{ns}}}EntityType"
        found = False
        for etype in root.iter(ns_tag):
            found = True
            yield etype
        if found:
            return  # первый совпавший namespace используем
    # Fallback: искать EntityType без namespace
    for etype in root.iter("EntityType"):
        yield etype


def _iter_properties(etype: ET.Element):
    """Итератор по Property внутри EntityType (любой namespace)."""
    for child in etype:
        tag = child.tag
        # Извлечь локальное имя тега без namespace
        local = tag.split("}")[-1] if "}" in tag else tag
        if local == "Property":
            yield child


def _parse_metadata_xml(xml_text: str) -> list[dict]:
    """Разбор XML $metadata → список {'name':..., 'label':...}."""
    entities: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
        for etype in _iter_entity_types(root):
            name = etype.get("Name", "")
            label = ""
            for prop in _iter_properties(etype):
                pname = prop.get("Name", "")
                if pname == "Description":
                    label = f"  (имеет Description)"
                    break
            entities.append({"name": name, "label": label})
    except ET.ParseError as e:
        log.error("Ошибка разбора $metadata: %s", e)
    return entities


def _parse_entity_fields(xml_text: str, entity_name: str) -> list[str]:
    """Извлечь имена свойств EntityType из XML."""
    fields: list[str] = []
    try:
        root = ET.fromstring(xml_text)
        for etype in _iter_entity_types(root):
            if etype.get("Name") == entity_name:
                for child in etype:
                    tag = child.tag
                    local = tag.split("}")[-1] if "}" in tag else tag
                    if local in ("Property", "NavigationProperty"):
                        pname = child.get("Name")
                        if pname:
                            fields.append(pname)
                break
    except ET.ParseError:
        pass
    return fields


async def fetch_metadata_from_server(odata_url: str, auth_header: str) -> str | None:
    """Загрузить $metadata с сервера 1С."""
    meta_url = odata_url.rstrip("/") + "/$metadata"
    try:
        async with httpx.AsyncClient(verify=False, timeout=30) as client:
            resp = await client.get(meta_url, headers={"Authorization": auth_header})
            resp.raise_for_status()
            return resp.text
    except Exception as e:
        log.error("Не удалось загрузить $metadata: %s", e)
        return None


# ---------------------------------------------------------------------------
# Кэш метаданных
# ---------------------------------------------------------------------------

class MetadataCache:
    """Кэш сущностей и полей 1С OData."""

    def __init__(self, cache_dir: str = ".cache") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._entities: list[dict] = []
        self._xml_raw: str = ""
        self._loaded_at: float = 0

    # -- persistence --

    def _cache_path(self) -> Path:
        return self.cache_dir / METADATA_FILE

    def load_from_disk(self) -> bool:
        """Загрузить кэш с диска. Возвращает True если кэш валиден."""
        p = self._cache_path()
        if not p.exists():
            return False
        try:
            data = json.loads(p.read_text("utf-8"))
            ts = data.get("timestamp", 0)
            if time.time() - ts > METADATA_CACHE_SECONDS:
                log.info("Кэш метаданных устарел (%.0f ч)", (time.time() - ts) / 3600)
                return False
            self._entities = data.get("entities", [])
            self._xml_raw = data.get("xml_raw", "")
            self._loaded_at = ts
            log.info("Метаданные загружены из кэша: %d сущностей", len(self._entities))
            return True
        except Exception as e:
            log.warning("Ошибка чтения кэша метаданных: %s", e)
            return False

    def save_to_disk(self) -> None:
        """Сохранить кэш на диск."""
        p = self._cache_path()
        data = {
            "timestamp": self._loaded_at,
            "entities": self._entities,
            "xml_raw": self._xml_raw,
        }
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        log.info("Кэш метаданных сохранён: %d сущностей", len(self._entities))

    # -- loading --

    def parse_and_store(self, xml_text: str) -> list[dict]:
        """Разобрать XML и сохранить в кэш."""
        self._xml_raw = xml_text
        self._entities = _parse_metadata_xml(xml_text)
        self._loaded_at = time.time()
        self.save_to_disk()
        return self._entities

    # -- queries --

    @property
    def entities(self) -> list[dict]:
        return self._entities

    def get_entity_fields(self, entity_name: str) -> list[str]:
        """Получить список полей сущности."""
        if not self._xml_raw:
            return []
        return _parse_entity_fields(self._xml_raw, entity_name)

    # Префиксы типов объектов 1С для подсказки в промпте
    _TYPE_PREFIXES = [
        "Catalog_", "Document_", "InformationRegister_",
        "AccumulationRegister_", "ChartOfAccounts_",
        "ChartOfCharacteristicTypes_", "ChartOfCalculationTypes_",
        "Processing_", "Report_", "Enum_", "Task_",
        "Sequence_", "ExchangePlan_",
    ]

    def search_entities(self, query: str, top: int = 20) -> list[str]:
        """Нечёткий поиск сущностей по ключевому слову.

        Ищет вхождение query (case-insensitive) в имя сущности.
        Если query пустой — возвращает все имена (до top).
        """
        if not self._entities:
            return []
        q_lower = query.lower().strip()
        results: list[str] = []
        # Приоритет 1: точное вхождение query в имя
        for e in self._entities:
            name = e["name"]
            if q_lower and q_lower in name.lower():
                results.append(name)
                if len(results) >= top:
                    return results
        # Приоритет 2: если ничего не найдено — попробовать без префикса типа
        if not results and "_" in q_lower:
            # "Document_Увольнение" → "Увольнение"
            short = q_lower.split("_", 1)[-1]
            if short != q_lower:
                return self.search_entities(short, top=top)
        return results

    def format_entity_list(self) -> str:
        """Форматировать список сущностей для промпта.

        Если сущностей много (>200), выводит только сводку по типам
        и подсказку использовать search_entities.
        """
        if not self._entities:
            return "(метаданные не загружены — используйте /refresh)"

        total = len(self._entities)
        if total <= 200:
            # Маленькая конфигурация — покажем всё
            lines: list[str] = []
            for e in self._entities:
                name = e["name"]
                lines.append(f"  - {name}")
            return "\n".join(lines)

        # Большая конфигурация — сводка по типам
        type_counts: dict[str, int] = {}
        for e in self._entities:
            name = e["name"]
            prefix = name.split("_")[0] + "_" if "_" in name else name
            type_counts[prefix] = type_counts.get(prefix, 0) + 1

        lines = [f"  Всего сущностей: {total}"]
        lines.append("  Типы объектов:")
        for prefix, count in sorted(type_counts.items()):
            lines.append(f"    {prefix}... — {count} шт.")
        lines.append("")
        lines.append("  ⚠️ Список слишком большой для отображения.")
        lines.append("  Используй инструмент search_entities(query='ключевое слово') для поиска нужной сущности.")
        lines.append("  Примеры: search_entities(query='Увольнение'), search_entities(query='Document_Увольнение')")
        return "\n".join(lines)

    @property
    def is_loaded(self) -> bool:
        return bool(self._entities)
