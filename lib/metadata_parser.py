#!/usr/bin/env python3
"""Парсинг $metadata XML для 1С OData.

Чистые функции без состояния — используются:
- ``bot.agents.odata.metadata`` — кэш метаданных бота
- ``mcp_servers.odata_server`` — MCP-сервер
- ``skills.1cconfinfo.scripts.odata-cfg-info`` — CLI-утилита
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Optional
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# EDM namespaces (1C OData использует разные версии)
# ---------------------------------------------------------------------------

EDMX_NS = "http://schemas.microsoft.com/ado/2007/06/edmx"
EDM_NAMESPACES = [
    "http://schemas.microsoft.com/ado/2009/11/edm",
    "http://schemas.microsoft.com/ado/2008/09/edm",
]


# ---------------------------------------------------------------------------
# Типы объектов 1С — префиксы → тип и русские названия
# ---------------------------------------------------------------------------

PREFIX_TO_TYPE: OrderedDict[str, str] = OrderedDict([
    ("Catalog_", "Catalog"),
    ("Document_", "Document"),
    ("InformationRegister_", "InformationRegister"),
    ("AccumulationRegister_", "AccumulationRegister"),
    ("AccountingRegister_", "AccountingRegister"),
    ("CalculationRegister_", "CalculationRegister"),
    ("ChartOfCharacteristicTypes_", "ChartOfCharacteristicTypes"),
    ("ChartOfAccounts_", "ChartOfAccounts"),
    ("ChartOfCalculationTypes_", "ChartOfCalculationTypes"),
    ("Enum_", "Enum"),
    ("BusinessProcess_", "BusinessProcess"),
    ("Task_", "Task"),
    ("ExchangePlan_", "ExchangePlan"),
    ("Sequence_", "Sequence"),
    ("DocumentJournal_", "DocumentJournal"),
])

TYPE_RU: dict[str, str] = {
    "Catalog": "Справочники",
    "Document": "Документы",
    "InformationRegister": "Регистры сведений",
    "AccumulationRegister": "Регистры накопления",
    "AccountingRegister": "Регистры бухгалтерии",
    "CalculationRegister": "Регистры расчёта",
    "ChartOfCharacteristicTypes": "ПВХ",
    "ChartOfAccounts": "Планы счетов",
    "ChartOfCalculationTypes": "ПВР",
    "Enum": "Перечисления",
    "BusinessProcess": "Бизнес-процессы",
    "Task": "Задачи",
    "ExchangePlan": "Планы обмена",
    "Sequence": "Последовательности",
    "DocumentJournal": "Журналы документов",
}

TYPE_ORDER: list[str] = list(PREFIX_TO_TYPE.values())


# ---------------------------------------------------------------------------
# XML helpers — namespace-agnostic find
# ---------------------------------------------------------------------------

def find_ns(elem: ET.Element, tag: str, ns_candidates: list[str]) -> Optional[ET.Element]:
    """Найти первый дочерний элемент ``tag`` в одном из namespace-кандидатов."""
    for ns in ns_candidates:
        found = elem.find(f"{{{ns}}}{tag}")
        if found is not None:
            return found
    return elem.find(tag)


def findall_ns(elem: ET.Element, tag: str, ns_candidates: list[str]) -> list[ET.Element]:
    """Найти все дочерние элементы ``tag`` в одном из namespace-кандидатов."""
    for ns in ns_candidates:
        results = elem.findall(f"{{{ns}}}{tag}")
        if results:
            return results
    return elem.findall(tag)


def _parse_root(xml_text: str) -> Optional[ET.Element]:
    """Безопасно разобрать XML текст в Element, логируя ошибки."""
    try:
        return ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.error("Ошибка разбора XML: %s", e)
        return None


# ---------------------------------------------------------------------------
# Итераторы по EntityType / Property
# ---------------------------------------------------------------------------

def iter_entity_types(root: ET.Element):
    """Итератор по всем ``EntityType`` из $metadata (поддержка разных namespace)."""
    for ns in EDM_NAMESPACES:
        ns_tag = f"{{{ns}}}EntityType"
        found = False
        for etype in root.iter(ns_tag):
            found = True
            yield etype
        if found:
            return  # первый совпавший namespace используем
    # Fallback: без namespace
    yield from root.iter("EntityType")


def iter_properties(etype: ET.Element):
    """Итератор по ``Property`` внутри ``EntityType`` (любой namespace)."""
    for child in etype:
        tag = child.tag
        local = tag.split("}")[-1] if "}" in tag else tag
        if local == "Property":
            yield child


def iter_nav_properties(etype: ET.Element):
    """Итератор по ``NavigationProperty`` внутри ``EntityType`` (любой namespace)."""
    for child in etype:
        tag = child.tag
        local = tag.split("}")[-1] if "}" in tag else tag
        if local == "NavigationProperty":
            yield child


# ---------------------------------------------------------------------------
# Парсинг EntitySet → Schema → EntityContainer
# ---------------------------------------------------------------------------

def find_schema(root: ET.Element) -> Optional[ET.Element]:
    """Найти элемент Schema внутри EDMX DataServices."""
    ns_list = EDM_NAMESPACES + [""]
    data_services = find_ns(root, "DataServices", [EDMX_NS])
    if data_services is None:
        data_services = root
    schema = find_ns(data_services, "Schema", ns_list)
    if schema is None:
        logger.error("Schema не найден в $metadata")
    return schema


def find_entity_sets(schema: ET.Element) -> list[ET.Element]:
    """Найти все EntitySet внутри EntityContainer."""
    ns_list = EDM_NAMESPACES + [""]
    container = find_ns(schema, "EntityContainer", ns_list)
    if container is None:
        return []
    return findall_ns(container, "EntitySet", ns_list)


def get_namespace(schema: ET.Element) -> str:
    """Получить Namespace из Schema (часто — имя конфигурации)."""
    return schema.get("Namespace", "")


# ---------------------------------------------------------------------------
# Основные функции парсинга
# ---------------------------------------------------------------------------

def parse_entity_sets(xml_text: str) -> list[dict]:
    """Разобрать XML $metadata → список ``{'name': ..., 'label': ...}``.

    Это список EntityType (не EntitySet).  Label помечает наличие Description.
    """
    root = _parse_root(xml_text)
    if root is None:
        return []

    entities: list[dict] = []
    for etype in iter_entity_types(root):
        name = etype.get("Name", "")
        label = ""
        for prop in iter_properties(etype):
            if prop.get("Name") == "Description":
                label = "  (имеет Description)"
                break
        entities.append({"name": name, "label": label})
    return entities


def parse_entity_fields(xml_text: str, entity_name: str) -> list[str]:
    """Извлечь имена свойств (Property + NavigationProperty) EntityType."""
    root = _parse_root(xml_text)
    if root is None:
        return []

    fields: list[str] = []
    for etype in iter_entity_types(root):
        if etype.get("Name") == entity_name:
            for child in etype:
                tag = child.tag
                local = tag.split("}")[-1] if "}" in tag else tag
                if local in ("Property", "NavigationProperty"):
                    pname = child.get("Name")
                    if pname:
                        fields.append(pname)
            break
    return fields


def search_entities(
    entities: list[dict],
    query: str,
    top: int = 20,
) -> list[str]:
    """Нечёткий поиск сущностей по ключевому слову.

    Args:
        entities: список ``{'name': ..., 'label': ...}``
        query: подстрока для поиска (case-insensitive)
        top: максимум результатов

    Returns:
        Список имён сущностей.
    """
    if not entities:
        return []

    q_lower = query.lower().strip()
    if not q_lower:
        return [e["name"] for e in entities[:top]]

    results: list[str] = []
    for e in entities:
        name = e["name"]
        if q_lower in name.lower():
            results.append(name)
            if len(results) >= top:
                return results

    # Если ничего не найдено — попробовать без префикса типа
    if not results and "_" in q_lower:
        short = q_lower.split("_", 1)[-1]
        if short != q_lower:
            return search_entities(entities, short, top=top)

    return results


# ---------------------------------------------------------------------------
# Классификация по типам 1С (для odata-cfg-info)
# ---------------------------------------------------------------------------

def classify_entity_sets(
    xml_text: str,
) -> tuple[OrderedDict, dict[str, list[str]], list[ET.Element], str]:
    """Классифицировать EntitySet-ы по типам объектов 1С.

    Returns:
        Кортеж ``(type_counts, type_names, entity_sets_elements, namespace)``:
        - **type_counts** — ``OrderedDict[type_name, count]``
        - **type_names** — ``dict[type_name, list[obj_name]]``
        - **entity_sets_elements** — список ``ET.Element`` EntitySet
        - **namespace** — ``Namespace`` из Schema
    """
    root = _parse_root(xml_text)
    if root is None:
        return OrderedDict(), {}, [], ""

    schema = find_schema(root)
    if schema is None:
        return OrderedDict(), {}, [], ""

    entity_sets = find_entity_sets(schema)
    namespace = get_namespace(schema)

    type_counts: OrderedDict[str, int] = OrderedDict()
    type_names: dict[str, list[str]] = {}

    for es in entity_sets:
        name = es.get("Name", "")
        matched_type = None
        matched_obj_name = None
        for prefix, type_name in PREFIX_TO_TYPE.items():
            if name.startswith(prefix):
                matched_type = type_name
                matched_obj_name = name[len(prefix):]
                break
        if matched_type and matched_obj_name is not None:
            if matched_type not in type_counts:
                type_counts[matched_type] = 0
                type_names[matched_type] = []
            type_counts[matched_type] += 1
            type_names[matched_type].append(matched_obj_name)

    return type_counts, type_names, entity_sets, namespace