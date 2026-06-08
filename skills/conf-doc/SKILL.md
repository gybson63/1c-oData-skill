---
name: conf-doc
description: >-
  Семантический поиск и drill-down по документации конфигурации 1С через MCP 1c-conf-doc
  или HTTP API. Используй для метаданных (реквизиты, регистры, связи) и источников данных
  отчётов (запросы СКД) перед OData-запросами. Короткие keyword-запросы; odata_fields
  на top-1; финальные имена полей — из OData $metadata.
---

# conf-doc — поиск по метаданным конфигурации 1С

Инструмент: **1c-conf-doc** (HTTP `:8050` или MCP `conf_doc_*`). Индекс SQLite + FAISS + markdown **на сервере** — не читай `output/`, `metadata.db`, CLI с диска клиента.

**Связка с OData:** conf-doc → объект, смысл полей, регистры из отчётов; OData `$metadata` / `fetch` → фактические имена полей и данные.

## MCP-инструменты

| MCP | HTTP | Назначение |
|-----|------|------------|
| `conf_doc_health` | GET `/health` | Доступность |
| `conf_doc_list_configurations` | GET `/configurations` | Имена проиндексированных конфигураций |
| `conf_doc_search` | POST `/search` | Семантический поиск (+ `odata_fields` для top-1) |
| `conf_doc_list_objects` | GET `/objects` | Поиск по имени в SQLite |
| `conf_doc_get_object` | GET `/objects/{type}/{name}` | Карточка: счётчики, чанки, help |
| `conf_doc_get_object_chunk` | GET `.../chunks/{N}` | Полный текст фрагмента |
| `conf_doc_query` | POST `/query` | RAG (только если LLM включён на сервере) |

OpenAPI: `{CONF_DOC_API_URL}/docs`

## Переменные окружения MCP

| Переменная | Описание |
|------------|----------|
| `CONF_DOC_API_URL` | URL backend без `/` (например `http://localhost:8050`) |
| `CONF_DOC_CONFIGURATION` | Поле **`name`** из `/configurations` (ЗУП 3.1: `ЗарплатаИУправлениеПерсоналомКОРП`) |

См. [`mcp.example.json`](../../mcp.example.json).

## Двухшаговая модель

1. **Обзор** — `conf_doc_search` / POST `/search`: топ объектов, один лучший чанк на объект.
2. **Детали** — `conf_doc_get_object` → `conf_doc_get_object_chunk` или уточнённый search.

По умолчанию `/search` обрезает `text` до **800 символов**. Передай `"full": true` для полного текста.
Поле **`odata_fields` не обрезается** — для **top-1** возвращается JSON реквизитов из индекса.

## Как формулировать query (критично)

Семантический поиск **плохо работает на длинных вопросах**. Не передавай целиком: *«Покажи 10 штатных сотрудников: ФИО, должность»*.

| Делай | Не делай |
|-------|----------|
| Короткие ключевые слова: `отпуск`, `увольнение`, `Сотрудники` | Полный текст вопроса с глаголами и числами |
| Точное имя объекта: `КадроваяИсторияСотрудников` | Размытые фразы: `Catalog Сотрудники` |
| Несколько узких запросов + выбор лучшего | Один длинный запрос «на всё» |
| Фильтр `object_type`: `Document`, `InformationRegister` | Ожидать, что Role подскажет OData-entity |

### Доменные доп. запросы (ЗУП)

| Тема | Дополнительные query |
|------|----------------------|
| Сотрудники, ФИО, должность | `Сотрудники`, `КадроваяИсторияСотрудников` |
| Отпуск, остаток отпуска | `отпуск`, `остатки отпусков`, `АналитикаОстатковОтпусков`, `ОстаткиОтпусков` |
| Начисление зарплаты | `НачислениеЗарплаты`, `начисление зарплаты` |
| Увольнение | `увольнение`, `Увольнение` |

### Приоритет типов для OData-entity

Предпочитай: **Catalog, Document, InformationRegister, AccumulationRegister, CalculationRegister**.
**Report** не даёт OData-entity, но его чанки «Запрос СКД» показывают **реальные регистры-источники** отчёта — читай их для выбора register entity.

## Чанки объектов

| chunk | Содержимое |
|-------|------------|
| `0` | Overview (справка, формы) |
| `1+` | Реквизиты шапки, табличные части |

**Report:** дополнительно `## Модуль объекта` (BSL) и `## Запрос СКД: {набор}` — полный текст запроса из основной СКД.
Для анализа источников данных **читай чанки с «Запрос СКД»**, не только overview.

**Пример (остатки отпусков):**

1. `search("остатки отпусков")` → `Report.ОстаткиОтпусков` (семантика) + `InformationRegister.АналитикаОстатковОтпусков` (прямой OData)
2. `get_object(Report, ОстаткиОтпусков)` → список чанков
3. `get_object_chunk(..., N)` где в тексте `Запрос СКД` → регистры: `НачальныеОстаткиОтпусков`, `ФактическиеОтпуска`, …
4. OData: для готового остатка — `InformationRegister_АналитикаОстатковОтпусков` + поле `ОстатокДней`; имена — из `$metadata`

## conf-doc + OData: два слоя правды

| Слой | Источник | Что даёт |
|------|----------|----------|
| Объект и смысл полей | conf-doc | `Document.Отпуск`, реквизиты, запросы отчётов |
| Имена полей в запросе к базе | OData `$metadata` | Фактические `Property` **публикации** |

`odata_fields` строится из **XML конфигурации**, не из OData-публикации. Перед `$select` / `$filter` **сверяй** с `$metadata`.

## Workflow для агента

### Структура конфигурации

```
- [ ] conf_doc_health
- [ ] conf_doc_list_configurations
- [ ] conf_doc_search — короткий query (+ odata_fields top-1)
- [ ] conf_doc_get_object_chunk — реквизиты / справка
```

### Перед OData-запросом к данным

```
- [ ] conf_doc_search — keyword-запросы (см. таблицу ЗУП)
- [ ] Выбрать Catalog/Document/Register из top-k
- [ ] Для отчётных тем — drill-down Report → чанки «Запрос СКД»
- [ ] odata_fields или chunk с реквизитами
- [ ] OData $metadata — финальные имена полей
- [ ] fetch / OData
```

## Маппинг типов → OData-префиксы

| Тип conf-doc | Префикс OData | Пример |
|--------------|---------------|--------|
| Catalog | `Catalog_` | `Catalog_Сотрудники` |
| Document | `Document_` | `Document_Отпуск` |
| InformationRegister | `InformationRegister_` | `InformationRegister_АналитикаОстатковОтпусков` |
| AccumulationRegister | `AccumulationRegister_` | `AccumulationRegister_ФактическиеОтпуска` |
| CalculationRegister | `CalculationRegister_` | `CalculationRegister_...` |

Имя `configuration` — поле **`name`** из `/configurations`, не синоним «ЗУП».

## Ограничения

- Нет прямого чтения `.md` / SQLite с клиента.
- `odata_fields` ≠ гарантия имён в OData публикации.
- Виртуальные таблицы OData (`/Balance()`, `/Turnovers()`) в conf-doc не описаны — см. OData skill.
- После обновления chunker (Report+СКД) нужен `index` на сервере conf-doc для ZUP.

## Ошибки

| Симптом | Действие |
|---------|----------|
| Connection refused | `docker compose up -d` в 1c-conf-doc |
| Report — только 1 короткий chunk | Переиндекс ZUP (`index --force` или смена CHUNKER_VERSION) |
| 503 на `conf_doc_query` | LLM отключён — search + chunks |
| OData 400 code 6 | Сверить с `$metadata`, не только conf-doc |

## Настройка

Backend: проект [`1c-conf-doc`](C:\ПервыйБИТ\ИИ\1c-conf-doc), порт **8050**.
Оценка пользы: [`docs/conf-doc-evaluation-checklist.md`](../../docs/conf-doc-evaluation-checklist.md).
