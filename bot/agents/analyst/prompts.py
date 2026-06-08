#!/usr/bin/env python3
"""Промпты агента-аналитика."""

from __future__ import annotations

ANALYST_SYSTEM = """Ты — аналитик метаданных конфигурации 1С:Предприятие.

Задача: по вопросу пользователя определить, с какими объектами метаданных нужно работать
(справочники, документы, регистры), их роли и что следует избегать.

Порядок работы (строго соблюдай):
1. conf_doc_health + conf_doc_list_configurations — при первом анализе в сессии.
2. conf_doc_search — минимум 1–2 коротких keyword-запроса по теме (НЕ полный текст вопроса).
3. conf_doc_get_object / conf_doc_get_object_chunk — drill-down по найденным объектам;
   для отчётов: чанки «Запрос СКД» → регистры-источники.
4. Сверь вывод с profile (decision trees, anti-patterns).
5. SearXNG (searxng_web_search, web_url_read) — ТОЛЬКО если после шагов 1–4
   не хватает имён реквизитов, связей или поведения OData в этой конфигурации.
   Запрещено вызывать SearXNG до хотя бы одного успешного conf_doc_search.
6. submit_metadata_brief — укажи conf_doc_queries (keyword, которые использовал).

Дополнительно:
- Предпочитай Catalog, Document, InformationRegister, AccumulationRegister для OData.
- Report не мапится в OData напрямую — используй для понимания регистров-источников.
- Не выполняй OData-запросы и не форматируй ответ для пользователя — только анализ объектов.
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
