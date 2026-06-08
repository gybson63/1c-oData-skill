---
name: analyst-conf-doc
description: >-
  Использование MCP conf-doc агентом-аналитиком: keyword-поиск, приоритет типов,
  drill-down Report → СКД. Фокус на выборе объектов, не на OData-синтаксисе.
---

# analyst-conf-doc — conf-doc для аналитика

Базовый skill: [`conf-doc`](../conf-doc/SKILL.md). Здесь — отличия для **аналитика**.

## Цель

Найти **какие объекты метаданных** релевантны вопросу, не строить OData-запрос.

## Keyword-запросы

| Делай | Не делай |
|-------|----------|
| `отпуск`, `АналитикаОстатковОтпусков` | Полный текст вопроса с числами |
| Несколько узких search | Один длинный query |

## Приоритет типов для OData-entity

1. Catalog, Document, InformationRegister, AccumulationRegister, CalculationRegister
2. Report — **не OData**, но источник регистров через СКД
3. Role, Subsystem, CommonModule — игнорировать для entity

## Report → регистры

1. `conf_doc_search("остатки отпусков")` → `Report.ОстаткиОтпусков`
2. `conf_doc_get_object(Report, ОстаткиОтпусков)` → список чанков
3. Чанк с «Запрос СКД» → имена регистров в запросе
4. Выбрать register entity для OData

## Маппинг тип → OData-префикс

| object_type | OData prefix |
|-------------|--------------|
| Catalog | Catalog_ |
| Document | Document_ |
| InformationRegister | InformationRegister_ |
| AccumulationRegister | AccumulationRegister_ |
| CalculationRegister | CalculationRegister_ |

## MCP-инструменты (MVP)

`conf_doc_health`, `conf_doc_list_configurations`, `conf_doc_search`,
`conf_doc_get_object`, `conf_doc_get_object_chunk`, `conf_doc_list_objects`

Не читай локальные `output/` и `metadata.db` — только MCP.
