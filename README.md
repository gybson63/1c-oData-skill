Публикация на ![Infostart](https://infostart.ru/bitrix/templates/sandbox_empty/assets/tpl/abo/img/logo.svg) https://infostart.ru/1c/articles/2686030

# 1c-oData-skill

Проект для работы с 1С:Предприятие через стандартный OData-интерфейс: скиллы для ИИ-агентов, Telegram-бот с мультиагентной архитектурой, внешние обработки.

---

## Telegram-бот для запросов к 1С

Бот принимает вопросы на русском языке и возвращает данные из базы 1С — без написания запросов вручную. Построен на мультиагентной архитектуре с поддержкой MCP (Model Context Protocol).

**Возможности:**
- Подбор нужного объекта по смыслу вопроса (`Catalog_ПодразделенияОрганизаций`, `Document_РеализацияТоваровУслуг` и т.д.)
- Двухшаговая обработка: AI формирует OData-запрос → AI форматирует ответ для Telegram
- Подсчёт записей (`Сколько сотрудников в базе?` → число через `/$count`)
- Автоматическое раскрытие ссылочных полей через `$expand` с приоритетами
- Инструменты для агента: справочник OData, поиск сущностей, список полей — через function calling
- Агент-форматтер: отдельный агент для красивого HTML-оформления ответов в Telegram
- MCP-клиент: подключение внешних MCP-серверов (stdio / SSE)
- Автоматический фоллбэк при отсутствии поддержки tool use у модели
- **Structured logging** — JSON-логи в файл, человекочитаемые в консоль, ротация по дате
- **Метрики** — счётчики, таймеры, трекинг AI-затрат с персистентной записью в JSONL
- **Cost Analyzer** — агрегация затрат на AI по часам/дням/неделям
- **Учёт токенов по сессиям** — подсчёт входящих/исходящих токенов и стоимости за каждый чат отдельно
- Файл `bot/config_hint.md` — описание терминологии вашей конфигурации (ЗУП, ERP, УТ и т.д.)

Подробное описание архитектуры, настройка и конфигурация — в [`bot/README.md`](bot/README.md).

### Быстрый запуск

```bash
pip install -r requirements.txt

# Скопировать и заполнить env.json
cp env.example.json env.json

python -m bot
# с параметрами:
python -m bot --env-file env.json --profile default --log-level DEBUG
```

### Конфигурация (env.json)

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

### Telegram-команды

- `/start` — приветствие, список агентов
- `/status` — статус всех агентов
- `/refresh` — обновить метаданные 1С
- `/tokens` — отчёт по токенам и стоимости текущей сессии
- Любой текст → маршрутизация агенту (по умолчанию — odata)

### Учёт токенов и стоимости

Бот автоматически отслеживает потребление токенов AI для каждого чата (сессии) отдельно — как основным агентом OData, так и агентом-форматтером.

**Что отслеживается:**
- 📥 **Входящие токены** (prompt_tokens) — текст запроса, история, системный промпт
- 📤 **Исходящие токены** (completion_tokens) — сгенерированный ответ AI
- 💰 **Стоимость** — в рублях (если провайдер возвращает `cost_rub`) или в долларах (расчёт по ценам из конфигурации `ai_pricing`)

**Как отображается:**

После каждого ответа бот добавляет компактную строку с текущими итогами сессии:

```
📊 📥3,200 📤1,500 | 💰₽2.15
```

Команда `/tokens` показывает детальный отчёт:

```
📊 Токены текущей сессии

Запросов: 5
📥 Входящие: 3,200
📤 Исходящие: 1,500
📋 Итого: 4,700

Стоимость: $0.0450 / ₽2.15
```

**Настройка цен** (в `env.json`):

```json
{
  "profiles": {
    "default": {
      "ai_pricing": {
        "models": {
          "gpt-4o-mini": { "input": 0.15, "output": 0.60 },
          "gpt-4o": { "input": 2.50, "output": 10.00 }
        }
      }
    }
  }
}
```

Цены указываются за 1 млн токенов в USD. Если провайдер (например, YandexGPT) возвращает стоимость в рублях через поле `cost_rub` в ответе, она отображается напрямую.

> При команде `/start` счётчики сессии сбрасываются. Глобальные метрики (счётчики, таймеры, CostLogger) при этом не затрагиваются.

---

## Скилл: OData-запросы к данным 1С

Основной скилл проекта — [`skills/odata/`](skills/odata/SKILL.md). Позволяет запрашивать данные любых объектов 1С через REST OData без написания кода.

### Через MCP (рекомендуется)

Скилл использует MCP-инструмент **`fetch`**. Для точных запросов подключите также **1c-conf-doc** (семантический поиск по метаданным конфигурации). Шаблон: [`mcp.example.json`](mcp.example.json), рабочий конфиг Cursor: [`.cursor/mcp.json`](.cursor/mcp.json).

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

Workflow: `conf_doc_search` → уточнение реквизитов → `fetch` к OData. См. [`skills/conf-doc/SKILL.md`](skills/conf-doc/SKILL.md).

Или через внешний `@modelcontextprotocol/server-fetch`:

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

См. [`skills/odata/SKILL.md`](skills/odata/SKILL.md).

### Через curl (альтернатива)

```bash
ODATA_URL=$(node -e "const d=require('./env.json').default; process.stdout.write(d.odata_url)")

ODATA_AUTH=$(node -e "const d=require('./env.json').default; process.stdout.write(Buffer.from(d.odata_user+':'+d.odata_password).toString('base64'))")

# URL-кодирование кириллицы
ENCODED=$(node -e "process.stdout.write(encodeURIComponent('Контрагенты'))")

curl -s -H "Authorization: Basic $ODATA_AUTH" -H "Accept: application/json" \
  "$ODATA_URL/Catalog_${ENCODED}?\$top=10&\$format=json"
```

> **Важно:** `curl -u "user:pass"` не работает с кириллицей на Windows — используйте только заголовок `Authorization: Basic`.

### Типы объектов в URL

| Тип 1С | Префикс в OData |
|--------|-----------------|
| Справочник | `Catalog_` |
| Документ | `Document_` |
| РегистрСведений | `InformationRegister_` |
| РегистрНакопления | `AccumulationRegister_` |
| РегистрРасчета | `CalculationRegister_` |
| ПланВидовРасчета | `ChartOfCalculationTypes_` |
| Перечисление | `Enum_` |

Подробнее — в [`skills/odata/SKILL.md`](skills/odata/SKILL.md).

---

## Внешняя обработка: включение объектов в OData

Обработка [`processing/EnableODataInterface.epf`](processing/) позволяет выбрать конкретные объекты конфигурации для публикации через OData — без Конфигуратора, прямо из режима Предприятия.

### Форма обработки

```
┌─────────────────────────────────────────────────────────────┐
│  [Применить]  [Выбрать все]  [Снять все]                    │
│  Текущий состав загружен. Опубликовано объектов: 342.       │
├─────────────────────────────────────────────────────────────┤
│  ▼ Справочники                                              │
│    ☑  Контрагенты                                           │
│    ☑  Сотрудники                                            │
│    ☐  ФизическиеЛица                                        │
│  ▼ Документы                                                │
│    ☑  НачислениеЗарплаты                                    │
│    ☐  ПриемНаРаботу                                         │
│  ▶ Регистры сведений                                        │
│  ▶ Регистры накопления                                      │
│  ...                                                        │
└─────────────────────────────────────────────────────────────┘
```

При открытии обработка читает текущий состав OData и расставляет флажки. При нажатии **«Применить»** — сохраняет только отмеченные объекты.

### Сборка EPF из исходников

```powershell
powershell.exe -NoProfile -File .claude/skills/epf-build/scripts/epf-build.ps1 `
    -SourceFile "processing/EnableODataInterface.xml" `
    -OutputFile "processing/EnableODataInterface.epf"
```

Если база занята конфигуратором — скрипт автоматически создаст временную базу-заглушку.

---

## MCP-серверы

### 1c-odata

Собственный MCP-сервер ([`mcp_servers/odata_server.py`](mcp_servers/odata_server.py)) для HTTP-запросов к 1С OData API с Basic-авторизацией. Инструменты: `fetch`, `fetch_table`, `analyze_data`. Транспорт: stdio.

### 1c-conf-doc

Внешний MCP из проекта [`1c-conf-doc`](C:\ПервыйБИТ\ИИ\1c-conf-doc): stdio-мост к HTTP API на `http://localhost:8050`. Инструменты: `conf_doc_search`, `conf_doc_get_object`, `conf_doc_get_object_chunk` и др. Индекс строится из XML-выгрузки конфигурации (Docker: `docker compose up -d` в 1c-conf-doc).

Проверка backend: `curl http://localhost:8050/health`. Имя для `CONF_DOC_CONFIGURATION` — поле **`name`** из `/configurations` (как в метаданных, например `ЗарплатаИУправлениеПерсоналомКОРП`, не сокращение «ЗУП»).

---

## Документация

| Файл | Описание |
|------|----------|
| [`bot/README.md`](bot/README.md) | Архитектура бота, агенты, конфигурация |
| [`skills/odata/SKILL.md`](skills/odata/SKILL.md) | OData-запросы: параметры, фильтры, примеры |
| [`skills/conf-doc/SKILL.md`](skills/conf-doc/SKILL.md) | Семантический поиск по метаданным через MCP 1c-conf-doc |
| [`skills/analyst/SKILL.md`](skills/analyst/SKILL.md) | Аналитик метаданных: MetadataBrief, /analyze |
| [`skills/analyst-conf-doc/SKILL.md`](skills/analyst-conf-doc/SKILL.md) | conf-doc для аналитика |
| [`skills/analyst-domain/SKILL.md`](skills/analyst-domain/SKILL.md) | Profile, decision trees |
| [`skills/analyst-mcp/SKILL.md`](skills/analyst-mcp/SKILL.md) | MCP-инструменты аналитика |
| [`docs/conf-doc-evaluation-checklist.md`](docs/conf-doc-evaluation-checklist.md) | Чеклист: оценка пользы conf-doc (10 вопросов ЗУП) |
| [`skills/1cconfinfo/SKILL.md`](skills/1cconfinfo/SKILL.md) | Анализ структуры конфигурации 1С (conf-doc / XML / OData) |
| [`docs/1c-value-tree-in-forms.md`](docs/1c-value-tree-in-forms.md) | Деревья значений в управляемых формах 1С |
| [`docs/user-guide.md`](docs/user-guide.md) | Руководство пользователя (не-IT) |
| [`docs/full-guide.md`](docs/full-guide.md) | Полное техническое руководство |
| [`processing/README.md`](processing/README.md) | Описание обработки EnableODataInterface |

---

## Структура проекта

```
bot/
  __init__.py                 — пакет
  __main__.py                 — точка входа (python -m bot)
  bot.py                      — Telegram handlers + роутер агентов
  config.py                   — Pydantic Settings конфигурация
  logging_config.py           — Structured logging (JSON в файл, текст в консоль)
  metrics.py                  — Метрики, CostLogger, CostAnalyzer
  history.py                  — Управление историей диалогов
  utils.py                    — утилиты (RateLimiter, load_config, esc_html)
  mcp_client.py               — MCP-клиент (stdio / SSE транспорты)
  master_prompt.md            — промпт форматирования ответов
  config_hint.md              — терминология конфигурации 1С
  README.md                   — описание бота
  agents/
    base.py                   — абстрактный класс BaseAgent
    odata/
      agent_1c_odata.py       — ODataAgent (двухшаговая обработка)
      prompts.py              — системные промпты и справочник OData
      metadata.py             — загрузка и кэширование $metadata
      odata_http.py           — HTTP-запросы к OData API
    formatter/
      agent_formatter.py      — FormatterAgent (Telegram HTML-форматирование)
      prompts.py              — промпт форматтера
bot_lib/
  __init__.py
  exceptions.py               — Иерархия типизированных исключений
  metadata_parser.py          — Парсинг $metadata XML
  odata_client.py             — Асинхронный HTTP-клиент OData с retry
logs/                         — Логи бота (автосоздание)
  costs/                      — JSONL-файлы AI-затрат по дням
tests/
  conftest.py                 — Общие фикстуры
  test_config.py              — Тесты конфигурации
  test_logging_config.py      — Тесты structured logging
  test_metrics.py             — Тесты метрик и CostAnalyzer
  test_metadata_parser.py     — Тесты парсинга метаданных
  test_odata_client.py        — Тесты OData HTTP-клиента
mcp_servers/
  odata_server.py             — MCP-сервер для 1С OData API
skills/
  odata/                      — скилл OData-запросов
  1cconfinfo/                 — скилл анализа конфигурации
    scripts/odata-cfg-info.py — скрипт анализа XML конфигурации
processing/
  EnableODataInterface.epf    — собранная обработка
  EnableODataInterface.xml    — метаданные
  EnableODataInterface/       — XML-исходники
docs/
  full-guide.md               — полное руководство
  1c-value-tree-in-forms.md   — гайд по деревьям в формах 1С
examples/
  check-availability.sh       — проверка доступности OData
  enable-odata.bsl            — включение OData через 1С-скрипт
  query-catalog.sh            — пример запроса к справочнику
env.json                      — конфигурация (не в git)
env.example.json              — пример конфигурации
requirements.txt              — Python-зависимости
```

## Требования

- **1С:Предприятие 8.3.6+** с опубликованным OData-интерфейсом
- **Python 3.10+** (для Telegram-бота)
- **Node.js + npm** (для MCP-сервера `@modelcontextprotocol/server-fetch` через `npx`, альтернативно — Python MCP-сервер)
- **PowerShell** (встроен в Windows — для сборки EPF)

### Python-зависимости

```
python-telegram-bot>=20.0
openai>=1.0.0
mcp>=1.0.0
httpx>=0.27.0
```

Установка: `pip install -r requirements.txt`
