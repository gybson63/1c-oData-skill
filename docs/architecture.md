# Архитектура проекта 1c-oData-skill

> Описание архитектурных решений для тех, кто проектирует и эксплуатирует AI-агентов.
> Проект — Telegram-бот, который позволяет пользователям делать запросы к данным 1С:Предприятие через OData REST API, используя AI для формирования запросов и форматирования ответов.

---

## Содержание

1. [Общая архитектура](#1-общая-архитектура)
2. [Система агентов](#2-система-агентов)
3. [Двухшаговый AI-пайплайн](#3-двухшаговый-ai-пайплайн)
4. [Выполнение инструментов (Tools)](#4-выполнение-инструментов-tools)
5. [Model Context Protocol (MCP)](#5-model-context-protocol-mcp)
6. [Skills — навыки агента](#6-skills--навыки-агента)
7. [Конфигурация](#7-конфигурация)
8. [Контроль бюджета и метрики](#8-контроль-бюджета-и-метрики)
9. [Устойчивость и обработка ошибок](#9-устойчивость-и-обработка-ошибок)
10. [Пагинация](#10-пагинация)
11. [Инфраструктура](#11-инфраструктура)
12. [Уроки и рекомендации](#12-уроки-и-рекомендации)

---

## 1. Общая архитектура

```
┌─────────────────────────────────────────────────────────────────┐
│                        Telegram User                            │
│                    (текстовые запросы)                           │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                   bot/bot.py — Роутер (thin)                     │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐  │
│  │ /start      │  │ /metrics     │  │ handle_message()       │  │
│  │ /status     │  │ /tokens      │  │   → ChatManager        │  │
│  │ /clear      │  │ /refresh     │  │   → Chat.process()     │  │
│  │ /history    │  │              │  │   → _send_telegram()   │  │
│  └─────────────┘  └──────────────┘  └──────────┬─────────────┘  │
└─────────────────────────────────────────────────┼────────────────┘
                                                   │
                                                   ▼
┌──────────────────────────────────────────────────────────────────┐
│           bot/chat.py — ChatManager + Chat                       │
│  ChatManager → get_or_create(chat_id) → Chat                    │
│    └─ Chat.process_message(text):                                │
│         Agent.process → Formatter → Truncate → Pagination       │
│         → ChatResponse(text, reply_markup)                       │
└──────────────────────────┬───────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│              AGENT_REGISTRY (паттерн Registry)                   │
│  ┌────────────────────────┐  ┌─────────────────────────────┐    │
│  │    ODataAgent          │  │    FormatterAgent           │    │
│  │  (основной обработчик) │  │  (пост-форматирование)      │    │
│  └────────┬───────────────┘  └─────────────────────────────┘    │
└───────────┼──────────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Двухшаговый AI-пайплайн                       │
│                                                                  │
│  Шаг 1: AI → JSON-запрос → Tool Calls → OData HTTP              │
│  Шаг 2: AI → форматирование ответа → HTML для Telegram          │
└────────────┬──────────────────────┬──────────────────────────────┘
             │                      │
             ▼                      ▼
┌──────────────────────┐  ┌───────────────────────────┐
│  MCP-серверы         │  │  1С OData REST API        │
│  (внешние tools)     │  │  ($metadata, CRUD)        │
└──────────────────────┘  └───────────────────────────┘
```

### Ключевые принципы

| Принцип | Реализация |
|---------|-----------|
| **Разделение ответственности** | Роутер → Агент → AI → HTTP — каждый слой делает одно дело |
| **Registry для агентов** | `AGENT_REGISTRY` — декларативный маппинг имён на классы |
| **Двухшаговый AI** | Шаг 1: запрос, Шаг 2: форматирование — разные промпты и температура |
| **Graceful degradation** | 4 уровня fallback при обработке tool calls |
| **Бюджетный контроль** | Трекинг токенов на глобальном, сессионном и по-запросном уровне |

---

## 2. Система агентов

### BaseAgent (абстрактный класс)

```python
class BaseAgent:
    name: str                           # идентификатор агента
    
    async def initialize(...)           # настройка: AI-клиент, MCP, кэш
    async def shutdown()                # очистка ресурсов
    async def refresh()                 # обновление данных (например, $metadata)
    async def process_message(...)      # обработка сообщения пользователя
    def get_status()                    # статус для /status
```

**Жизненный цикл агента:**

```
main() → post_init() → init_agents()
         ├─ AGENT_REGISTRY["odata"] → ODataAgent()
         │   ├─ initialize(): AI client, MCP, $metadata cache
         │   └─ готов к обработке
         └─ FormatterAgent (авто-создаётся если не задан явно)
             
shutdown() → post_shutdown() → shutdown_agents()
             ├─ agent.shutdown() для каждого
             └─ MCP disconnect_all()
```

### ODataAgent

Основной агент (1199 строк). Реализует:
- Загрузку и кэширование `$metadata` из 1С
- Двухшаговую AI-обработку запросов
- Валидацию полей ($select, $orderby) по метаданным
- Построение $expand для раскрытия ссылок
- Пагинацию через inline-кнопки Telegram

### FormatterAgent

Пост-обработчик: принимает уже готовый ответ ODataAgent и прогоняет через отдельный AI-вызов для финального форматирования. Это позволяет разделить «логику запроса» и «красивое оформление».

---

## 3. Двухшаговый AI-пайплайн

Это ключевое архитектурное решение. Вместо одного AI-вызова используется два:

### Шаг 1: AI формирует OData-запрос

```
Пользователь: "Покажи организации из Москвы"
                    │
                    ▼
         Системный промпт + $metadata + история + tools
                    │
                    ▼
              AI (temperature=0.1)
                    │
            ┌───────┴───────┐
            │   Tool calls?  │
            │   (до 2 раундов)│
            └───────┬───────┘
                    │
                    ▼
         JSON: {"entity": "Catalog_Организации",
                "filter": "Адрес eq 'Москва'",
                "select": "Ref, Description, ИНН"}
```

- **Низкая температура** (0.1) — нужен точный JSON, без «творчества»
- **Function calling** — AI вызывает инструменты для поиска сущностей и полей
- **До 2 раундов tool calls** — ограничение для предотвращения зацикливания

### Шаг 2: AI форматирует ответ

```
OData-ответ (JSON) → AI (temperature=0.3) → HTML для Telegram
```

- **Более высокая температура** (0.3) — допустима вариативность в формулировках
- Входные данные: выборка записей (обрезанная до `max_sample_records` и `max_data_length`)
- Выход: HTML с таблицами, эмодзи, подсветкой

### Почему два шага, а не один?

| Критерий | Один вызов | Два вызова |
|----------|-----------|-----------|
| Точность запроса | Низкая — смешивает запрос и форматирование | Высокая — каждый шаг сфокусирован |
| Использование tools | Ограничено — контекст перегружен | Полное — Step 1 полностью посвящён поиску |
| Стоимость | Ниже (1 вызов) | Выше (2 вызова), но Step 2 дешёвый |
| Качество ответа | Среднее | Высокое — каждый промпт заточен под задачу |

---

## 4. Выполнение инструментов (Tools)

### Иерархия вызова инструментов

Проект реализует **4 уровня fallback** для выполнения инструментов — от нативного function calling до текстового regex-парсинга:

```
┌─────────────────────────────────────────────────┐
│  Уровень 1: OpenAI Function Calling (нативный)  │
│  AI возвращает tool_calls в API-ответе          │
│  → _resolve_tool_calls() — до 2 раундов        │
└──────────────────┬──────────────────────────────┘
                   │ не поддерживается моделью?
                   ▼
┌─────────────────────────────────────────────────┐
│  Уровень 2: Inline JSON tool call               │
│  AI пишет в content: {"name":"search_entities", │
│      "arguments":{"query":"..."}}               │
│  → _is_inline_tool_call() + resolve            │
└──────────────────┬──────────────────────────────┘
                   │ не распознано?
                   ▼
┌─────────────────────────────────────────────────┐
│  Уровень 3: Text tool call (regex)              │
│  AI пишет текстом: search_entities(query='...') │
│  → _TEXT_TOOL_RE regex → _resolve_text_tool    │
└──────────────────┬──────────────────────────────┘
                   │ не найдено?
                   ▼
┌─────────────────────────────────────────────────┐
│  Уровень 4: Auto-search по ключевому слову      │
│  → _guess_entity_from_text() — извлечение из   │
│    вопроса пользователя                         │
│  → _retry_with_search_results()                 │
└─────────────────────────────────────────────────┘
```

### Доступные инструменты

| Инструмент | Назначение | Данные |
|-----------|-----------|--------|
| `search_entities(query)` | Поиск сущности по ключевому слову | Из кэша $metadata |
| `get_entity_fields(entity_name)` | Получить поля сущности | Из кэша $metadata |
| `odata_reference(topic)` | Справка по OData (фильтры, синтаксис) | Статический словарь |
| MCP `fetch(url)` | HTTP-запрос к OData (внешний MCP-сервер) | Через MCP-протокол |

### Обнаружение поддержки function calling

При первом вызове агент пытается использовать `tools` + `tool_choice="auto"`. Если модель возвращает `BadRequestError` с упоминанием «tool use» / «tool_choice» / «functions», агент переключается в режим без инструментов (`self._tools_supported = False`) и перестраивает системный промпт с инструкциями для текстового вызова.

---

## 5. Model Context Protocol (MCP)

### Что такое MCP?

Model Context Protocol — открытый протокол для подключения внешних инструментов к AI-агентам. В проекте используется для **расширения возможностей агента** за счёт внешних серверов.

### Архитектура MCP-подключения

```
env.json → {"mcp_servers": {"odata": {...}}}
                │
                ▼
      MCPClientManager.connect_all()
                │
                ▼
      MCPServerConnection (per-server)
         │
         ├─ stdio: запуск subprocess
         │   (python mcp_servers/odata_server.py)
         │
         └─ sse: HTTP SSE-подключение
             (url: "http://...")

      После подключения:
         ├─ list_tools() → кэш инструментов
         ├─ _mcp_tool_to_openai() → формат OpenAI function calling
         └─ call_tool(name, args) → выполнение
```

### Ключевые решения

**1. Background-task pattern**

MCP-клиент использует специальный паттерн для избежания anyio scope errors:

```python
class MCPServerConnection:
    async def connect(self):
        self._bg_task = asyncio.create_task(self._bg_run())
        await self._ready.wait()  # ждём сигнала из background task

    async def _bg_run(self):
        async with stdio_client(...) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                self._ready.set()  # сигнал: готовы
                await asyncio.Future()  # «паркуем» задачу навсегда
```

Вся жизнь контекстных менеджеров (`stdio_client`, `ClientSession`) протекает **внутри одного asyncio Task**. Это обходит ограничение anyio: «cancel scope entered / exited in different tasks».

**2. Конвертация MCP → OpenAI function calling**

Инструменты MCP автоматически конвертируются в формат OpenAI:

```python
def _mcp_tool_to_openai(tool):
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.inputSchema,
        },
    }
```

Это позволяет прозрачно использовать MCP-инструменты как tools в OpenAI API.

**3. Маршрутизация инструментов**

```python
class MCPClientManager:
    _tool_to_server: dict[str, str]  # tool_name → server_name
    
    async def call_tool(tool_name, arguments):
        server_name = self._tool_to_server[tool_name]
        return await self._servers[server_name].call_tool(...)
```

Дедупликация: если два сервера экспортируют инструмент с одинаковым именем, используется первый подключённый.

### MCP-сервер odata_server.py

Единственный инструмент `fetch(url)` — универсальный HTTP-клиент для OData:

```
fetch(url="/odata/standard.odata/Catalog_Организации?$top=5")
  → ODataClient.get_entities(...) → JSON → text response
```

Сервер читает конфигурацию из переменных окружения и лениво инициализирует `ODataClient`.

---

## 6. Skills — навыки агента

### Структура навыков

```
skills/
├── odata/
│   └── SKILL.md         # Справочник OData-запросов
│                         # Entity prefixes, field suffixes,
│                         # $filter operators, примеры
│
└── 1cconfinfo/
    ├── SKILL.md         # Документация по анализу конфигурации
    └── scripts/
        └── odata-cfg-info.py  # CLI-утилита анализа $metadata
```

### Как используются Skills

Skills — это **статические знания**, которые передаются AI через системный промпт:

```python
# prompts.py
ODATA_REFERENCE = {
    "filter": "Операторы: eq, ne, gt, ge, lt, le, and, or, not...",
    "select": "$select=Field1,Field2...",
    "expand": "$expand=Ref_Field($select=Description)...",
    # ... полный справочник OData v3
}

# Системный промпт включает справочник:
STEP1_SYSTEM = """
...список сущностей из $metadata...
Для уточнения полей используй инструменты.
Справочник OData: {reference}
"""
```

### SKILL.md — контекст для Cline (VS Code AI)

Файлы `SKILL.md` также служат контекстом для Cline — AI-ассистента в VS Code. Когда разработчик просит Cline что-то сделать с OData-запросами, Cline автоматически загружает `skills/odata/SKILL.md` и использует содержащиеся там знания.

### Два подхода к получению метаданных

| Подход | Когда | Источник |
|--------|-------|----------|
| `$metadata` через OData | Бот запущен, 1С доступна | HTTP-запрос к 1С |
| `odata-cfg-info.py` | Оффлайн-анализ | XML-файл выгрузки конфигурации |

---

## 7. Конфигурация

### Иерархия конфигурации

```
env.json
└── profiles
    └── default (или именованный профиль)
        ├── telegram_token
        ├── ai_api_key, ai_base_url, ai_model
        ├── odata: { url, user, password, default_top, ... }
        ├── telegram: { timeouts, retries }
        ├── agents: { odata: { type, mcp_servers }, ... }
        ├── formatter: { enabled, model, temperature }
        ├── history: { max_turns, persist_dir }
        └── ai_pricing: { per_model: { model: { input, output } } }
```

### Pydantic Settings

Все настройки типизированы через Pydantic модели:

```python
class AppSettings(BaseModel):
    ai: AISettings              # API-ключ, модель, RPM, температура
    bot: BotSettings            # Telegram-токен
    telegram: TelegramTransportSettings  # таймауты, ретраи
    odata_query: ODataQuerySettings     # лимиты запросов
    formatter: FormatterSettings        # модель форматтера
    history: HistorySettings            # история диалогов
    ai_pricing: PricingSettings         # цены за токены (per-model)
```

**Singleton-паттерн**: `load_settings()` создаёт глобальный экземпляр, `get_settings()` возвращает его из любого модуля.

### Per-model ценообразование

```json
{
  "ai_pricing": {
    "input_per_1m": 0.15,
    "output_per_1m": 0.60,
    "per_model": {
      "gpt-4o-mini": { "input_per_1m": 0.15, "output_per_1m": 0.60 },
      "deepseek-chat": { "input_per_1m": 0.27, "output_per_1m": 1.10 }
    }
  }
}
```

---

## 8. Контроль бюджета и метрики

### Трёхуровневая система учёта

```
┌─────────────────────────────────────────────────────┐
│  Уровень 1: Per-request (MetricsRegistry)           │
│  Каждый AI-вызов → track_ai_usage()                 │
│  • input_tokens, output_tokens                      │
│  • cost_usd (расчётный), cost_rub (от провайдера)   │
│  • сохранение ответа провайдера в JSON              │
├─────────────────────────────────────────────────────┤
│  Уровень 2: Per-session (SessionTokenTracker)       │
│  Аккумуляция по chat_id                             │
│  • отображается в /tokens и в подписи к ответу      │
│  • сбрасывается при /clear                          │
├─────────────────────────────────────────────────────┤
│  Уровень 3: Глобальный (CostLogger + CostAnalyzer)  │
│  JSONL-файлы по дням: logs/costs/costs_YYYY-MM-DD  │
│  Агрегация по интервалам (минута → месяц)           │
│  • /metrics — сводка за сессию                      │
│  • CostAnalyzer.summary("day") — аналитика          │
└─────────────────────────────────────────────────────┘
```

### CostLogger — персистентное логирование

Каждый AI-вызов записывается в JSONL:

```json
{
  "ts": "2026-05-04T01:25:00.123456+04:00",
  "model": "gpt-4o-mini",
  "input_tokens": 1250,
  "output_tokens": 380,
  "cost_usd": 0.000413,
  "cost_rub": 0.035,
  "input_price_per_1m": 0.15,
  "output_price_per_1m": 0.60
}
```

Файлы ротируются автоматически — один файл на календарный день.

### CostAnalyzer — агрегация

```python
analyzer = CostAnalyzer("logs/costs")

# Затраты по дням
for bucket in analyzer.aggregate("day"):
    print(bucket)
    # [2026-05-04] day: 45 req, IN=56000, OUT=17000, $0.0186

# Сводка за период
summary = analyzer.summary("hour", since=start, until=end)
```

Поддерживаемые интервалы: `minute`, `5min`, `15min`, `30min`, `hour`, `6h`, `12h`, `day`, `week`, `month`.

### Метрики производительности

Помимо токенов, трекаются:

| Метрика | Тип | Описание |
|---------|-----|----------|
| `odata_requests` | Counter | Количество OData-запросов |
| `ai_requests_step1` | Counter | AI-вызовы Шага 1 |
| `ai_requests_step2` | Counter | AI-вызовы Шага 2 |
| `ai_step1` | Timer | Время Шага 1 (включая tool calls) |
| `ai_step2` | Timer | Время Шага 2 (форматирование) |
| `odata_get_entities` | Timer | Время HTTP-запроса к 1С |

### Rate Limiting

```python
class RateLimiter:
    """Ограничение RPM (requests per minute)."""
    async def wait(self):
        # Токен-ведро: не более N запросов в минуту
```

Каждый вызов AI проходит через RateLimiter с настраиваемым `rpm` (по умолчанию 20).

### Provider Response Logging

Все запросы и ответы AI сохраняются в `logs/<session_id>/NNN_step.json`:

```
logs/
├── 20260504_142530/        # session_id
│   ├── 001_step1.json      # запрос + ответ AI
│   ├── 002_step2.json
│   └── 003_step1.json
└── costs/
    ├── costs_2026-05-04.jsonl
    └── costs_2026-05-05.jsonl
```

Это позволяет post-mortem анализировать качество AI-ответов и отлаживать промпты.

---

## 9. Устойчивость и обработка ошибок

### Иерархия исключений

```
ODataSkillError (базовый)
├── ODataError          # HTTP-ошибки 1С (401, 404, 500)
├── AIError             # ошибки AI-провайдера
│   ├── AIRateLimitError   # 429 Too Many Requests
│   └── AIResponseError    # пустой ответ, некорректный формат
├── ConfigError         # ошибки конфигурации
└── QueryError          # ошибка разбора запроса
```

### Стратегии обработки

**OData-ошибки:**
- `401` → «Ошибка авторизации»
- `404` → «Объект не найден»
- `5xx` → «Ошибка сервера 1С»
- Коды 1С (0, 6, 8, 9, 14) → человекопонятные подсказки

**Fallback при 0 записей:**
1. Убрать date-фильтр (если в фильтре есть `Number`)
2. Заменить `Number eq '...'` на `substringof('...', Number)` (OData v3)

**Telegram-ошибки:**
- `TimedOut` → ретрай до N раз с задержкой
- `BadRequest` (невалидный HTML) → fallback на plain text
- Polling-ошибки → auto-restart с задержкой

**AI-ошибки:**
- Rate limit → сообщение «подождите»
- Model не поддерживает tools → auto-downgrade на текстовый режим

### Retry-политика для OData HTTP-запросов

Используется `tenacity`:

```python
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
)
```

---

## 10. Пагинация

### Механизм

Пагинация реализована через Telegram inline-кнопки:

```
[Пользователь видит 20 записей] → [➡️ Следующие]
                                        │
                            callback_data="page:20"
                                        │
                            execute_page(chat_id, skip=20)
                                        │
                            AI Step 2 форматирует новую страницу
                            (с «хвостом» предыдущей для контекста)
```

### Контекст пагинации

После каждого запроса в историю сохраняется JSON с контекстом:

```json
{
  "entity": "Catalog_Организации",
  "filter": "Город eq 'Москва'",
  "select": "Ref,Description,ИНН",
  "top": 20,
  "skip": 0,
  "total": 156,
  "shown": 20,
  "expand": "...",
  "explanation": "Организации из Москвы"
}
```

При нажатии «Следующие» агент **не проходит через AI Step 1** — он использует сохранённый контекст и выполняет OData-запрос напрямую. Только Step 2 (форматирование) вызывается заново.

---

## 11. Инфраструктура

### Docker

```yaml
# docker-compose.yml
services:
  bot:
    build: .
    restart: unless-stopped
    volumes:
      - ./env.json:/app/env.json:ro    # конфигурация (read-only)
      - bot-cache:/app/.cache           # кэш $metadata
      - bot-logs:/app/logs              # логи и затраты
```

### Кэширование

- **$metadata**: XML-файл в `.cache/`, TTL = 24 часа (настраивается)
- **При старте**: загрузка из диска, при отсутствии — с сервера 1С
- **Команда /refresh**: принудительное обновление

### История диалогов

```python
class HistoryManager:
    max_messages: int     # абсолютный максимум (default: 100)
    trim_to: int          # обрезка до (default: 60)
    persist_dir: str|None # директория для персистентности (опционально)
```

История обрезается автоматически при достижении `max_messages`, оставляя последние `trim_to` сообщений. Для AI-контекста используется `history_max_turns` (по умолчанию 10 пар = 20 сообщений).

---

## 12. Уроки и рекомендации

### Что работает хорошо

1. **Двухшаговый пайплайн** — разделение «понимания запроса» и «форматирования» значительно повышает качество обоих этапов. Разные температуры для шагов — ключевой тьюнинг.

2. **4 уровня fallback для tools** — позволяет работать с моделями, которые не поддерживают function calling (многие российские и open-source модели).

3. **Background-task паттерн для MCP** — решает проблему anyio scope errors и обеспечивает стабильное подключение к MCP-серверам.

4. **Трёхуровневый учёт затрат** — JSONL-логи с агрегацией дают полную прозрачность расходов на AI.

5. **Кэширование $metadata** — один запрос к 1С при старте, потом всё из кэша. `/refresh` для обновления по требованию.

### Проблемы и ограничения

1. **Cost Logger не интегрирован с chat_id** — CostLogger не получает `chat_id` при записи в JSONL. Это связано с тем, что `track_ai_usage()` вызывается из агента, а `chat_id` передаётся через `ContextVar`. Требуется доработка.

2. **Один агент за раз** — роутер всегда направляет запрос на `_default_agent()`. Нет механизма маршрутизации по намерению (intent routing).

3. **Нет ограничения на общую стоимость** — есть учёт, но нет хард-лимита «остановить при $X». Без внешнего мониторинга можно потратить больше, чем планировалось.

4. **Inline tool calls — хрупкий механизм** — regex-парсинг текстовых вызовов инструментов (`search_entities(query='...')`) может ломаться на сложных аргументах с кавычками.

### Рекомендации для новых AI-агентов

| Область | Рекомендация |
|---------|-------------|
| **Промпты** | Разделяйте «понимание» и «форматирование» на отдельные AI-вызовы с разными температурами |
| **Tools** | Всегда реализуйте fallback для моделей без function calling |
| **MCP** | Используйте background-task паттерн для контекстных менеджеров anyio |
| **Бюджет** | Логируйте каждый AI-вызов в JSONL, агрегируйте по интервалам, показывайте пользователю |
| **Метаданные** | Кэшируйте на диск с TTL, не запрашивайте при каждом обращении |
| **Ошибки** | Иерархия исключений + человекопонятные сообщения для каждого типа ошибки |
| **История** | Обрезка с «гладким» переходом (trim_to), а не резкий обрыв |
| **Пагинация** | Сохраняйте контекст запроса, не проходите через AI повторно для следующих страниц |
| **Конфигурация** | Pydantic Settings с валидацией — рано узнаете об ошибках |

---

## Сводка технологий

| Компонент | Технология |
|-----------|-----------|
| Telegram | python-telegram-bot >= 20.0 |
| AI | OpenAI Python SDK (любой OpenAI-совместимый API) |
| Chat-слой | `bot/chat.py` (ChatManager + Chat + ChatResponse) |
| MCP | mcp >= 1.0 (stdio + SSE transport) |
| HTTP | httpx + tenacity (retry) |
| Конфигурация | Pydantic + Pydantic Settings |
| Логирование | structlog + стандартный logging |
| Метрики | собственная реализация (MetricsRegistry + JSONL) |
| Контейнеризация | Docker + docker-compose |
| Тестирование | pytest + pytest-asyncio + respx (HTTP mocking) |
