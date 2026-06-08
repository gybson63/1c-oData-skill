---
name: analyst-mcp
description: >-
  MCP-инструменты агента-аналитика 1С: shared и per-agent серверы,
  conf-doc, SearXNG web-search и document-reader.
---

# analyst-mcp — MCP для аналитика

> Человеческая документация (Docker, отладка, gating): [`docs/searxng.md`](../../docs/searxng.md). Конфиг env.json: [`docs/configuration.md`](../../docs/configuration.md).

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
        "searxng": {
          "enabled": true,
          "transport": "stdio",
          "command": "npx",
          "args": ["-y", "mcp-searxng"],
          "env": {
            "SEARXNG_URL": "http://localhost:8080"
          }
        },
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
| **searxng** | Только после conf_doc + profile; не хватает имён реквизитов / OData-поведения | `searxng_web_search`, `web_url_read` |
| **document-reader** | Книги/PDF по 1С (future) | filesystem MCP |

## SearXNG

Fallback после conf-doc + profile. **Gating:** не вызывать до `conf_doc_search`.

| Tool | Назначение |
|------|------------|
| `searxng_web_search` | Поиск по вебу |
| `web_url_read` | Чтение страницы в markdown |

Установка, Docker, MCP в Cursor, отладка 403: **[`docs/searxng.md`](../../docs/searxng.md)**.

## Cursor IDE

Подключи в `.cursor/mcp.json`:

- `1c-conf-doc` — обязательно
- `searxng` — опционально для внешней документации
- Опционально: filesystem (document-reader)

См. [`mcp.analyst.example.json`](../../mcp.analyst.example.json)

## Whitelist (бот)

`agents.analyst.allowed_mcp_tools` — только эти MCP tools попадают в AI loop.

По умолчанию включены `searxng_web_search` и `web_url_read`; они активны только при `searxng.enabled: true`.

Native tool `submit_metadata_brief` — всегда доступен для финализации.

## Отладка

Логи бота: `AnalystAgent MCP [server]: tools=[...]`, `Analyst MCP: brief ready`.

HTTP fallback: `conf_doc_fallback` в env.json при недоступности MCP conf-doc.

SearXNG 403 и smoke-test: [`docs/searxng.md`](../../docs/searxng.md).
