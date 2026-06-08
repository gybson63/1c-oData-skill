#!/usr/bin/env python3
"""Промпты агента-аналитика."""

from __future__ import annotations

ANALYST_SYSTEM = """Ты — аналитик метаданных конфигурации 1С:Предприятие.

Задача: по вопросу пользователя определить, с какими объектами метаданных нужно работать
(справочники, документы, регистры), их роли и что следует избегать.

Правила:
1. Используй MCP-инструменты conf_doc_* для поиска по конфигурации (короткие keyword-запросы).
2. Для отчётных тем — drill-down Report → чанки «Запрос СКД» (регистры-источники).
3. Предпочитай Catalog, Document, InformationRegister, AccumulationRegister для OData.
4. Report не мапится в OData напрямую — используй для понимания регистров-источников.
5. Сверяй вывод с profile конфигурации (anti-patterns, decision trees).
6. Когда анализ готов — вызови submit_metadata_brief с итоговой структурой.

Не выполняй OData-запросы и не форматируй ответ для пользователя — только анализ объектов.
{profile_block}
"""

SUBMIT_METADATA_BRIEF_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_metadata_brief",
        "description": "Зафиксировать итоговый анализ метаданных для OData-агента",
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "description": "Краткий доменный intent"},
                "primary_objects": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "meta_type": {"type": "string"},
                            "name": {"type": "string"},
                            "odata_entity": {"type": "string"},
                            "role": {"type": "string", "enum": ["primary", "join", "source", "avoid"]},
                            "reason": {"type": "string"},
                        },
                        "required": ["meta_type", "name", "role"],
                    },
                },
                "secondary_objects": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "meta_type": {"type": "string"},
                            "name": {"type": "string"},
                            "odata_entity": {"type": "string"},
                            "role": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                    },
                },
                "avoid": {"type": "array", "items": {"type": "string"}},
                "conf_doc_queries": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "string"},
            },
            "required": ["intent", "primary_objects"],
        },
    },
}

OBJECT_TYPE_TO_ODATA: dict[str, str] = {
    "Catalog": "Catalog_",
    "Document": "Document_",
    "DocumentJournal": "DocumentJournal_",
    "InformationRegister": "InformationRegister_",
    "AccumulationRegister": "AccumulationRegister_",
    "CalculationRegister": "CalculationRegister_",
    "AccountingRegister": "AccountingRegister_",
    "Enum": "Enum_",
}


def meta_to_odata_entity(meta_type: str, name: str) -> str:
    """Построить OData entity из типа и имени метаданных."""
    prefix = OBJECT_TYPE_TO_ODATA.get(meta_type, "")
    if prefix:
        return f"{prefix}{name}"
    return name
