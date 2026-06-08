# Конфигурация (env.json)

Канонический справочник по файлу `env.json`. Пример-шаблон: [`env.example.json`](../env.example.json).

> Файл `env.json` не коммитится в git. Скопируйте из `env.example.json` и заполните секреты.

---

## Иерархия

```text
profiles
  └── <profile_name>          # например "default"
        ├── telegram_token, ai_*, history, telegram, odata
        ├── mcp_servers       # shared MCP для всех агентов
        ├── agents            # odata, analyst, …
        ├── formatter
        ├── ai_pricing
        └── email             # опциональный email-интерфейс
```

Профиль выбирается флагом `--profile default` при запуске бота.

---

## Минимальный рабочий профиль

```json
{
  "profiles": {
    "default": {
      "telegram_token": "TELEGRAM_BOT_TOKEN",
      "ai_api_key": "OPENAI_API_KEY",
      "ai_base_url": "https://api.openai.com/v1",
      "ai_model": "gpt-4o-mini",
      "ai_rpm": 20,

      "agents": {
        "odata": {
          "type": "odata",
          "odata_url": "http://localhost/YourBase/odata/standard.odata",
          "odata_user": "Администратор",
          "odata_password": "пароль",
          "mcp_servers": {
            "odata": {
              "command": "python",
              "args": ["mcp_servers/odata_server.py"],
              "env": {
                "ODATA_URL": "http://localhost/YourBase/odata/standard.odata",
                "ODATA_USER": "Администратор",
                "ODATA_PASSWORD": "пароль"
              }
            }
          }
        }
      },

      "formatter": {
        "enabled": true,
        "formatter_model": "gpt-4o-mini"
      }
    }
  }
}
```

---

## AI и лимиты

| Поле | Описание |
|------|----------|
| `ai_api_key` | Ключ API провайдера |
| `ai_base_url` | Base URL (OpenAI-compatible) |
| `ai_model` | Модель для OData Step 1 |
| `ai_rpm` | Rate limit запросов в минуту |
| `ai_temperature` / `ai_temperature_step2` | Температура Step 1 / Step 2 |
| `history.max_turns` | Глубина истории диалога |

### Цены на токены (`ai_pricing`)

Цены за 1 млн токенов в USD (если провайдер не возвращает `cost_rub`):

```json
{
  "ai_pricing": {
    "input_per_1m": 0.15,
    "output_per_1m": 0.60
  }
}
```

Или per-model (см. полный `env.example.json`). Команда `/tokens` и подпись под ответами используют эти значения.

Подробнее об учёте: [`user-guide.md`](user-guide.md) §«Контроль расходов», [`architecture.md`](architecture.md) §8.

---

## Shared MCP (`mcp_servers`)

Общие серверы профиля, наследуются агентами при `mcp_inherit: true`:

```json
{
  "mcp_servers": {
    "conf-doc": {
      "transport": "stdio",
      "command": "C:\\path\\to\\1c-conf-doc\\.venv\\Scripts\\python.exe",
      "args": ["-m", "onec_conf_doc.mcp"],
      "env": {
        "CONF_DOC_API_URL": "http://localhost:8050",
        "CONF_DOC_CONFIGURATION": "ЗарплатаИУправлениеПерсоналомКОРП"
      }
    }
  }
}
```

Merge: `{...profile.mcp_servers, ...agent.mcp_servers}`. `enabled: false` — сервер пропускается.

Cursor MCP (отдельный файл): [`mcp-setup.md`](mcp-setup.md).

---

## Агент OData

| Поле | Описание |
|------|----------|
| `odata_url`, `odata_user`, `odata_password` | Доступ к 1С OData REST |
| `conf_doc.enabled` | HTTP-обогащение Step 1 без MCP |
| `conf_doc.configuration` | Точное `name` из `/configurations` |
| `conf_doc.enrich_prompt` | Вставка результатов поиска в промпт |
| `mcp_servers.odata` | MCP-сервер [`mcp_servers/odata_server.py`](../mcp_servers/odata_server.py) |

---

## Агент Analyst

| Поле | Описание |
|------|----------|
| `profile_path` | Domain profile, напр. `skills/analyst/profiles/zup-korp.md` |
| `preprocessor_for_odata` | Блок MetadataBrief перед OData Step 1 |
| `max_tool_iterations` | Лимит tool calls (по умолчанию 12) |
| `allowed_mcp_tools` | Whitelist MCP-инструментов |
| `conf_doc_fallback` | HTTP fallback при недоступности MCP conf-doc |

Режимы: `/analyze` в Telegram, `[analyze]` в email. Архитектура: [`bot/README.md`](../bot/README.md).

### SearXNG (per-agent MCP)

```json
{
  "mcp_servers": {
    "searxng": {
      "enabled": false,
      "command": "npx",
      "args": ["-y", "mcp-searxng"],
      "env": { "SEARXNG_URL": "http://localhost:8080" }
    }
  }
}
```

Docker, gating conf-doc→SearXNG, отладка: **[`searxng.md`](searxng.md)** — не дублируйте здесь.

Быстрое включение: `python scripts/enable_searxng_mcp.py`.

---

## Formatter

```json
{
  "formatter": {
    "enabled": true,
    "formatter_model": "gpt-4o-mini",
    "temperature": 0.2
  }
}
```

Отдельный AI-агент для HTML-оформления ответов в Telegram.

---

## Email-интерфейс

Секция `email` в профиле (`enabled: false` по умолчанию). IMAP/SMTP, allowed_senders, лимиты вложений.

Настройка и использование: **[`email.md`](email.md)**. Тестирование: [`email-testing.md`](email-testing.md).

---

## Терминология конфигурации

Файл [`bot/config_hint.md`](../bot/config_hint.md) — краткие подсказки для OData Step 1. Полный domain profile — в `skills/analyst/profiles/`.

---

## Связанные документы

- [`getting-started.md`](getting-started.md) — установка и чеклист
- [`mcp-setup.md`](mcp-setup.md) — MCP для Cursor
- [`searxng.md`](searxng.md) — SearXNG
- [`architecture.md`](architecture.md) — Pydantic Settings, merge MCP
