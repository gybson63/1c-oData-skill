# Настройка MCP для Cursor и агентов

Подключение Model Context Protocol к 1С OData и conf-doc. Для SearXNG см. отдельный гайд: **[`searxng.md`](searxng.md)**.

---

## Файлы конфигурации

| Файл | Назначение |
|------|------------|
| [`mcp.example.json`](../mcp.example.json) | OData + conf-doc для Cursor |
| [`mcp.analyst.example.json`](../mcp.analyst.example.json) | conf-doc + SearXNG для аналитика |
| [`.cursor/mcp.json`](../.cursor/mcp.json) | Рабочий конфиг Cursor (локальный, не в git) |
| `env.json` → `mcp_servers` | MCP для Telegram-бота ([`configuration.md`](configuration.md)) |

---

## 1c-odata + 1c-conf-doc (рекомендуется)

```json
{
  "mcpServers": {
    "1c-odata": {
      "command": "python",
      "args": ["mcp_servers/odata_server.py"],
      "env": {
        "ODATA_URL": "http://localhost/YourBase/odata/standard.odata",
        "ODATA_USER": "Администратор",
        "ODATA_PASSWORD": "пароль"
      }
    },
    "1c-conf-doc": {
      "command": "C:\\ПервыйБИТ\\ИИ\\1c-conf-doc\\.venv\\Scripts\\python.exe",
      "args": ["-m", "onec_conf_doc.mcp"],
      "env": {
        "CONF_DOC_API_URL": "http://localhost:8050",
        "CONF_DOC_CONFIGURATION": "ЗарплатаИУправлениеПерсоналомКОРП"
      }
    }
  }
}
```

### Workflow

```text
conf_doc_search (keyword) → conf_doc_get_object / chunk → fetch (OData)
```

Скиллы: [`skills/conf-doc/SKILL.md`](../skills/conf-doc/SKILL.md), [`skills/odata/SKILL.md`](../skills/odata/SKILL.md).

---

## Альтернатива: server-fetch через npx

```json
{
  "mcpServers": {
    "1c-odata": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-fetch"]
    }
  }
}
```

Basic-авторизацию для 1С OData нужно передавать в URL или заголовках внутри `fetch`. Собственный [`odata_server.py`](../mcp_servers/odata_server.py) удобнее для 1С.

---

## 1c-conf-doc: проверка

```bash
curl http://localhost:8050/health
curl http://localhost:8050/configurations
```

`CONF_DOC_CONFIGURATION` — поле **`name`** из ответа (как в метаданных, например `ЗарплатаИУправлениеПерсоналомКОРП`, не сокращение «ЗУП»).

Индекс строится из XML-выгрузки конфигурации (Docker в проекте 1c-conf-doc: `docker compose up -d`).

---

## MCP-серверы проекта

### 1c-odata

[`mcp_servers/odata_server.py`](../mcp_servers/odata_server.py) — HTTP к 1С OData с Basic-авторизацией.

Инструменты: `fetch`, `fetch_table`, `analyze_data`. Транспорт: stdio.

### 1c-conf-doc

Внешний MCP из проекта [1c-conf-doc](https://github.com/your-org/1c-conf-doc): stdio-мост к HTTP API.

Инструменты: `conf_doc_search`, `conf_doc_get_object`, `conf_doc_get_object_chunk` и др.

---

## SearXNG

Веб-поиск для аналитика метаданных — отдельный документ:

**→ [`docs/searxng.md`](searxng.md)**

---

## Связанные документы

- [`full-guide.md`](full-guide.md) — OData workflow без IDE
- [`configuration.md`](configuration.md) — MCP в env.json для бота
- [`architecture.md`](architecture.md) §5 — архитектура MCP в боте
