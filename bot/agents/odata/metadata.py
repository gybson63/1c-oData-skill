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

def _parse_metadata_xml(xml_text: str) -> list[dict]:
    """Разбор XML $metadata → список {'name':..., 'label':...}."""
    entities: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
        ns = {"edmx": "http://schemas.microsoft.com/ado/2007/06/edmx",
              "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
              "d": "http://schemas.microsoft.com/ado/2008/09/edm"}

        schemas = root.findall(".//edmx:DataServices/d:Schema", ns)
        if not schemas:
            schemas = root.findall(".//{http://schemas.microsoft.com/ado/2008/09/edm}Schema")
        for schema in schemas:
            for etype in schema.findall("d:EntityType", ns) if schemas else schema.findall(
                    "{http://schemas.microsoft.com/ado/2008/09/edm}EntityType"):
                name = etype.get("Name", "")
                label = ""
                for prop in etype.findall("d:Property", ns) if schemas else etype.findall(
                        "{http://schemas.microsoft.com/ado/2008/09/edm}Property"):
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
        ns_d = "http://schemas.microsoft.com/ado/2008/09/edm"
        for schema in root.iter(f"{{{ns_d}}}Schema"):
            for etype in schema.findall(f"{{{ns_d}}}EntityType"):
                if etype.get("Name") == entity_name:
                    for prop in etype.findall(f"{{{ns_d}}}Property"):
                        pname = prop.get("Name")
                        if pname:
                            fields.append(pname)
                    for nav in etype.findall(f"{{{ns_d}}}NavigationProperty"):
                        nname = nav.get("Name")
                        if nname:
                            fields.append(nname)
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

    def format_entity_list(self) -> str:
        """Форматировать список сущностей для промпта."""
        if not self._entities:
            return "(метаданные не загружены — используйте /refresh)"
        lines: list[str] = []
        for e in self._entities:
            name = e["name"]
            lines.append(f"  - {name}")
        return "\n".join(lines)

    @property
    def is_loaded(self) -> bool:
        return bool(self._entities)