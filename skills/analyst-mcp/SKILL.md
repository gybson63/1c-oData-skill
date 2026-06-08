---
name: analyst-mcp
description: >-
  MCP-инструменты агента-аналитика 1С: shared и per-agent серверы,
  conf-doc, будущие web-search и document-reader.
---

# analyst-mcp — MCP для аналитика

## Конфигурация (бот)

```json
{
  "mcp_servers": {
    "conf-doc": { "...": "shared для всех агентов" }
  },
  "agents": {
    "analyst": {
      "mcp_inherit": true,
      "mcp_servers": {
        "web-search": { "enabled": false },
        "document-reader": { "enabled": false }
      }
    }
  }
}
```

Merge: `{...profile.mcp_servers, ...agent.mcp_servers}`. `enabled: false` — пропуск.

## Когда какой MCP

| MCP | Когда | Инструменты |
|-----|-------|-------------|
| **conf-doc** | Всегда для метаданных конфигурации | `conf_doc_*` |
| **web-search** | conf-doc/profile не дали ответ; нужна внешняя документация | Brave Search (future) |
| **document-reader** | Книги/PDF по 1С (future) | filesystem MCP |

## Cursor IDE

Подключи в `.cursor/mcp.json`:

- `1c-conf-doc` — обязательно
- Опционально: Brave Search, filesystem

См. [`mcp.example.json`](../../mcp.example.json)

## Whitelist (бот)

`agents.analyst.allowed_mcp_tools` — только эти MCP tools попадают в AI loop.

Native tool `submit_metadata_brief` — всегда доступен для финализации.

## Отладка

Логи бота: `AnalystAgent MCP [server]: tools=[...]`, `Analyst MCP: brief ready`.

HTTP fallback: `conf_doc_fallback` в env.json при недоступности MCP.
