---
name: analyst-mcp
description: >-
  MCP-инструменты агента-аналитика 1С: shared и per-agent серверы,
  conf-doc, SearXNG web-search и document-reader.
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

## SearXNG: требования

### Быстрый старт (Docker)

```bash
docker compose -f docker-compose.searxng.yml up -d
python scripts/enable_searxng_mcp.py   # включить в env.json (один раз)
python scripts/test_searxng_mcp.py     # проверка MCP
```

Конфиг SearXNG: `docker/searxng/settings.yml` (JSON-формат уже включён).

### Компоненты

1. Запущенный экземпляр SearXNG с JSON-форматом в `settings.yml`:
   ```yaml
   search:
     formats:
       - html
       - json
   ```
2. `SEARXNG_URL` — URL инстанса (например `http://localhost:8080`).
3. Пакет `mcp-searxng` через `npx -y mcp-searxng` (Node.js).

### Инструменты SearXNG

| Tool | Назначение |
|------|------------|
| `searxng_web_search` | Поиск по вебу (`query`, опционально `pageno`, `time_range`, `language`) |
| `web_url_read` | Чтение страницы в markdown (`url`, опционально `maxLength`, `section`) |

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

Если SearXNG возвращает 403 — проверь `formats: [html, json]` в settings.yml.
