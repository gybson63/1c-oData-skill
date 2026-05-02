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
- Любой текст → маршрутизация агенту (по умолчанию — odata)

---

## Скилл: OData-запросы к данным 1С

Основной скилл проекта — [`skills/odata/`](skills/odata/SKILL.md). Позволяет запрашивать данные любых объектов 1С через REST OData без написания кода.

### Через MCP (рекомендуется)

Скилл использует MCP-инструмент **`fetch`**. Настройте MCP-сервер в конфигурации вашего агента (Cline, Claude Desktop, VS Code, Cursor):

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
    }
  }
}
```

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

## MCP-сервер для 1С OData

Собственный MCP-сервер ([`mcp_servers/odata_server.py`](mcp_servers/odata_server.py)) для выполнения HTTP-запросов к 1С OData API с автоматической Basic-авторизацией. Поддерживает GET, POST, PATCH, DELETE. Запускается через stdio-транспорт.

---

## Документация

| Файл | Описание |
|------|----------|
| [`bot/README.md`](bot/README.md) | Архитектура бота, агенты, конфигурация |
| [`skills/odata/SKILL.md`](skills/odata/SKILL.md) | OData-запросы: параметры, фильтры, примеры |
| [`skills/1cconfinfo/SKILL.md`](skills/1cconfinfo/SKILL.md) | Анализ XML-выгрузки конфигурации 1С |
| [`docs/1c-value-tree-in-forms.md`](docs/1c-value-tree-in-forms.md) | Деревья значений в управляемых формах 1С |
| [`docs/full-guide.md`](docs/full-guide.md) | Полное руководство |
| [`processing/README.md`](processing/README.md) | Описание обработки EnableODataInterface |

---

## Структура проекта

```
bot/
  __init__.py                 — пакет
  __main__.py                 — точка входа (python -m bot)
  bot.py                      — Telegram handlers + роутер агентов
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