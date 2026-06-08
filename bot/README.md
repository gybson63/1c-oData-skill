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
    ├── analyst/            # Аналитик метаданных (MCP conf-doc → MetadataBrief)
    └── odata/              # OData-агент
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
          "conf_doc": {
            "enabled": true,
            "api_url": "http://localhost:8050",
            "configuration": "ЗарплатаИУправлениеПерсоналомКОРП",
            "enrich_prompt": true,
            "search_top_k": 5
          },
          "mcp_servers": {
            "odata": {
              "command": "python",
              "args": ["mcp_servers/odata_server.py"],
              "env": { ... }
            },
            "conf-doc": {
              "command": "C:\\ПервыйБИТ\\ИИ\\1c-conf-doc\\.venv\\Scripts\\python.exe",
              "args": ["-m", "onec_conf_doc.mcp"],
              "env": {
                "CONF_DOC_API_URL": "http://localhost:8050",
                "CONF_DOC_CONFIGURATION": "ЗарплатаИУправлениеПерсоналомКОРП"
              }
            }
          }
        }
      }
    }
  }
}
```

### AnalystAgent (анализ метаданных)

Агент `analyst` определяет релевантные объекты метаданных через MCP `conf_doc_*` и profile MD (`skills/analyst/profiles/`).

| Режим | Как |
|-------|-----|
| Standalone | `/analyze <вопрос>` в Telegram, префикс `[analyze]` в email |
| Pre-step | `preprocessor_for_odata: true` — блок «АНАЛИЗ МЕТАДАННЫХ» в OData Step 1 |

**MCP:** shared серверы в `profiles.default.mcp_servers`, per-agent в `agents.analyst.mcp_servers` (merge через `bot/mcp_config.py`).

### conf-doc (обогащение Step 1)

Поле `configuration` — точное **`name`** из `GET http://localhost:8050/configurations` (имя в метаданных, например `ЗарплатаИУправлениеПерсоналомКОРП`), не человекочитаемый synonym.

Секция `conf_doc` в конфиге агента `odata` включает семантический поиск по метаданным конфигурации перед построением OData-запроса. Бот обращается к HTTP API напрямую (`bot_lib/conf_doc_client.py`), без зависимости от venv 1c-conf-doc — это работает и в Docker (`api_url: http://host.docker.internal:8050`).

При `enabled: true` и `enrich_prompt: true` в логах появится `conf_doc: enriched prompt with N results`.

## Логирование и метрики

### Structured Logging

Бот использует **структурированное логирование** с двумя форматами вывода:

| Поток | Формат | Назначение |
|-------|--------|------------|
| **Файл** | JSON (`{"ts": "...", "level": "INFO", "name": "module", "message": "..."}`) | Машинный парсинг, интеграция с ELK/Grafana Loki |
| **Консоль** | Человекочитаемый (`2026-05-04 12:00:00 [INFO    ] module: message`) | Разработка, отладка |

**Функции:**
- Ротация лог-файлов по дате/сессии (`logs/bot_20260504_sessionid.log`)
- Auto-cleanup файлов старше 30 дней
- Фильтр дедупликации — подавление повторяющихся ошибок
- structlog-совместимый bridge для будущей миграции

```bash
# Уровень логирования через CLI или env.json:
python -m bot --log-level DEBUG
```

HTTP-запросы (Telegram, OData, AI) логируются в канал `1c-bot.http`:
- **ERROR** — `http_request_failed` с `service`, `method`, `url` (с маскировкой токенов), `endpoint`, `error_type`, `elapsed_ms`
- **DEBUG** — `http_request_start` / `http_request_ok` для трассировки всех исходящих запросов

### Метрики (`bot/metrics.py`)

In-memory реестр метрик для мониторинга работы бота:

| Компонент | Описание |
|-----------|----------|
| **Счётчики** | `increment("name")` — подсчёт событий (запросы, ошибки) |
| **Таймеры** | `record_timer("name", seconds)` — min/max/avg время операций |
| **AI Usage** | Токены, стоимость по моделям |
| **CostLogger** | Персистентная запись AI-затрат в JSONL (`logs/costs/costs_YYYY-MM-DD.jsonl`) |
| **CostAnalyzer** | Агрегация затрат по интервалам (час / день / неделя / месяц) |

```python
# Async context manager для замеров:
async with track_time("odata_get_entities"):
    data = await client.get_entities(...)

# Декоратор для синхронных функций:
@track_sync("parse_metadata")
def parse(xml):
    ...

# AI-трекинг с расчётом стоимости:
registry.track_ai_usage(
    model="gpt-4o-mini",
    input_tokens=1000,
    output_tokens=500,
    input_price_per_1m=0.15,
    output_price_per_1m=0.60,
)
```

**Файлы затрат** (один JSONL-файл на день):
```jsonl
{"ts": "2026-05-04T12:00:00+04:00", "model": "gpt-4o-mini", "input_tokens": 1000, "output_tokens": 500, "cost_usd": 0.00045, "chat_id": 123456}
```

**Агрегация затрат:**
```python
analyzer = CostAnalyzer("logs/costs")
print(analyzer.summary("day"))   # Дневная сводка
print(analyzer.summary("hour"))  # Часовая сводка
print(f"Total: ${analyzer.total_cost():.4f}")
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
