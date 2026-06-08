---
name: analyst-domain
description: >-
  Доменные модели и decision trees для аналитика 1С: чтение profile MD,
  anti-patterns, типовые связки объектов по темам (ЗУП и др.).
---

# analyst-domain — доменный анализ

## Profile MD

Единый источник доменных знаний: `skills/analyst/profiles/<slug>.md`

Структура:

1. Идентификация (`conf_doc_configuration`)
2. Термины → OData entity
3. Доменные модели
4. Decision trees по темам
5. Anti-patterns (404, неверные VT)
6. Report → Register

## Decision tree (шаблон)

```
Если вопрос про X:
  1. Проверить profile → primary objects
  2. conf_doc_search: keyword1, keyword2
  3. Если Report в top-k → drill-down СКД
  4. Исключить объекты из anti-patterns
  5. MetadataBrief
```

## Anti-patterns (общие)

| Паттерн | Риск |
|---------|------|
| IR без VT, запрос к шапке | 400 — нужен `_RecordType` |
| AccumulationRegister `/Balance()` | Может быть не опубликован → `/Turnovers()` |
| Report как OData entity | Report не мапится в OData |
| `odata_fields` vs `$metadata` | Имена полей — только из OData публикации |

## ЗУП

Profile: [`../analyst/profiles/zup-korp.md`](../analyst/profiles/zup-korp.md)

Ключевые темы: сотрудники (кадровая история), отпуска (документ vs остатки), зарплата, увольнения.

## Дополнение profile

После разбора кейса (см. `docs/case-*.md`) — обновить decision tree и anti-patterns в profile.
