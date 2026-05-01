# 1С OData Telegram Bot — Multi-Agent Architecture

## Структура

```
bot/
├── bot.py                  # Точка входа: Telegram handlers + роутер агентов
├── utils.py                # Общие утилиты (RateLimiter, load_config, esc_html)
├── mcp_client.py           # MCP-клиент (Model Context Protocol)
├── config_hint.md          # Подсказки по конфигурации
├── master_prompt.md        # Общий промпт бота
│
└── agents/
    ├── __init__.py
    ├── base.py             # Абстрактный класс BaseAgent
    │
    └── odata/              # OData-агент
        ├── __init__.py
        ├── agent.py        # Класс ODataAgent (двухшаговая обработка)
        ├── prompts.py      # Системные промпты и справочник OData
        ├── metadata.py     # Загрузка и кэширование $metadata
        └── odata_http.py   # HTTP-запросы к OData API
```

## Архитектура агентов

Каждый агент наследует `BaseAgent` и реализует:

| Метод | Описание |
|-------|----------|
| `initialize(agent_config, global_config, ...)` | Инициализация: MCP, AI-клиент, кэш |
| `shutdown()` | Корректное завершение |
| `refresh()` | Обновление данных |
| `process_message(user_text, history)` | Обработка сообщения → (answer, history) |
| `get_status()` | Статус агента |

### Добавление нового агента

1. Создать папку `bot/agents/my_agent/` с файлом `agent.py`:
   ```python
   from ..base import BaseAgent

   class MyAgent(BaseAgent):
       name = "my_agent"
       # ... реализация методов
   ```

2. Зарегистрировать в `bot/bot.py`:
   ```python
   AGENT_REGISTRY: dict[str, type[BaseAgent]] = {
       "odata": ODataAgent,
       "my_agent": MyAgent,  # ← добавить
   }
   ```

3. Добавить секцию в `env.json`:
   ```json
   {
     "profiles": {
       "default": {
         "agents": {
           "odata": { ... },
           "my_agent": {
             "type": "my_agent",
             "...": "..."
           }
         }
       }
     }
   }
   ```

## Конфигурация (env.json)

```json
{
  "profiles": {
    "default": {
      "telegram_token": "...",
      "ai_api_key": "...",
      "ai_base_url": "https://api.openai.com/v1",
      "ai_model": "gpt-4o-mini",
      "ai_rpm": 20,

      "agents": {
        "odata": {
          "type": "odata",
          "odata_url": "http://host/base/odata/standard.odata",
          "odata_user": "Администратор",
          "odata_password": "пароль",
          "mcp_servers": {
            "odata": {
              "command": "python",
              "args": ["mcp_servers/odata_server.py"],
              "env": { ... }
            }
          }
        }
      }
    }
  }
}
```

## Запуск

```bash
python -m bot
# с параметрами:
python -m bot --env-file env.json --profile default --log-level DEBUG
```

## Команды Telegram

- `/start` — приветствие, список агентов
- `/status` — статус всех агентов
- `/refresh` — обновить данные агентов (метаданные 1С)
- Любой текст → маршрутизация агенту по умолчанию (odata)