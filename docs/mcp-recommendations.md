# Рекомендации MCP-серверов для проекта 1c-oData-skill

## Архитектура проекта

Проект представляет собой Telegram-бота, который через OData REST API запрашивает данные из 1С:Предприятие, а ИИ (OpenAI-совместимый) формирует и форматирует запросы. Уже есть инфраструктура подключения MCP-серверов через `bot/mcp_client.py` (stdio + SSE транспорта, конфигурация в `env.json` → `mcp_servers`).

---

## 🔥 Высший приоритет — прямая польза для бота

### 1. SQLite MCP Server (`@anthropic/mcp-server-sqlite` или аналог)

**Зачем:** Кеширование метаданных, история запросов, аналитика обращений.
Сейчас метаданные кешируются в `.cache/metadata_summary.json` и `.cache/odata_metadata_*.xml`. SQLite через MCP даст ИИ-агенту возможность делать SQL-запросы к кэшу — например, «какие объекты содержат поле ИНН?» или «сколько запросов было к Document_ за неделю?».

```json
"sqlite": {
  "transport": "stdio",
  "command": "npx",
  "args": ["-y", "@anthropic/mcp-server-sqlite", "--db-path", ".cache/bot_cache.db"]
}
```

### 2. Fetch MCP Server (`@anthropic/mcp-server-fetch`)

**Зачем:** Прямые HTTP-запросы из агента — проверка доступности OData, диагностика, отладка.
Бот уже делает HTTP-запросы через `urllib`, но MCP-fetch даст ИИ возможность самостоятельно проверять эндпоинты, тестировать URL, диагностировать ошибки 400/404 без жёсткого кодирования логики.

```json
"fetch": {
  "transport": "stdio",
  "command": "npx",
  "args": ["-y", "@anthropic/mcp-server-fetch"]
}
```

### 3. PostgreSQL / Database MCP Server

**Зачем:** Если 1С работает с PostgreSQL (частый случай), ИИ сможет напрямую анализировать структуру БД, выполнять SQL-запросы для отладки, сравнивать данные OData с данными в базе.

```json
"postgres": {
  "transport": "stdio",
  "command": "npx",
  "args": ["-y", "@anthropic/mcp-server-postgres", "postgresql://user:pass@localhost/1c_db"]
}
```

---

## ⚡ Высокий приоритет — расширение возможностей

### 4. Filesystem MCP Server (`@anthropic/mcp-server-filesystem`)

**Зачем:** Управление файлами проекта — чтение/запись конфигурации, логов, отчётов. ИИ сможет обновлять `config_hint.md`, читать логи бота, генерировать файлы отчётов по результатам OData-запросов.

```json
"filesystem": {
  "transport": "stdio",
  "command": "npx",
  "args": ["-y", "@anthropic/mcp-server-filesystem", "--root", "C:/ПервыйБИТ/ИИ/1c-oData-skill"]
}
```

### 5. Custom 1C-OData MCP Server (свой сервер)

**Зачем:** **Самое ценное.** Создать собственный MCP-сервер, который инкапсулирует всю логику работы с OData 1С — получение метаданных ($metadata), запросы с фильтрами, подсчёт записей, навигация по связям. Сейчас это захардкожено в `bot.py`. Вынос в MCP-сервер даст:

- Переиспользование в других клиентах (не только Telegram-бот)
- Чистую архитектуру: бот → MCP → OData
- Возможность подключать к любому MCP-совместимому ИИ (Claude, GPT и т.д.)

```json
"1c-odata": {
  "transport": "stdio",
  "command": "python",
  "args": ["mcp_servers/odata_server.py"],
  "env": {
    "ODATA_URL": "http://localhost/your_base/odata/standard.odata",
    "ODATA_USER": "Администратор",
    "ODATA_PASSWORD": "пароль"
  }
}
```

**Инструменты сервера:**

| Инструмент | Описание |
|------------|----------|
| `odata_list_entities` | Список всех опубликованных сущностей из $metadata |
| `odata_get_entity_fields` | Поля конкретной сущности (реквизиты, типы) |
| `odata_query` | Выполнить OData-запрос с фильтрами, $select, $top |
| `odata_count` | Подсчёт записей ($count) |
| `odata_get_metadata` | Получить полный $metadata XML/JSON |
| `odata_get_entity_record` | Получить конкретную запись по Ref_Key |

---

## 🛠 Средний приоритет — DevOps и мониторинг

### 6. Memory MCP Server (`@anthropic/mcp-server-memory`)

**Зачем:** Персистентная память между сессиями. Запоминание предпочтений пользователей, часто используемых запросов, контекста диалогов.

```json
"memory": {
  "transport": "stdio",
  "command": "npx",
  "args": ["-y", "@anthropic/mcp-server-memory"]
}
```

### 7. Sequential Thinking MCP Server (`@anthropic/mcp-server-sequential-thinking`)

**Зачем:** Улучшение качества рассуждений ИИ при сложных запросах — многошаговые фильтры, агрегации, аналитика. Особенно полезно для Gemini/YandexGPT, которые слабее в reasoning.

```json
"thinking": {
  "transport": "stdio",
  "command": "npx",
  "args": ["-y", "@anthropic/mcp-server-sequential-thinking"]
}
```

### 8. Sentry / Logging MCP Server

**Зачем:** Мониторинг ошибок бота в реальном времени. ИИ сможет просматривать ошибки, анализировать тренды, предлагать исправления.

---

## 📊 Низкий приоритет — полезные дополнения

### 9. Grafana MCP Server

**Зачем:** Если есть мониторинг — ИИ может читать метрики, строить дашборды по активности OData-запросов.

### 10. Git MCP Server

**Зачем:** Управление версиями конфигурации, просмотр истории изменений `config_hint.md` и промптов.

---

## 📋 Итоговая рекомендуемая конфигурация `env.json`

```json
{
  "default": {
    "odata_url": "http://localhost/your_base/odata/standard.odata",
    "odata_user": "Администратор",
    "odata_password": "пароль",
    "telegram_token": "1234567890:AAF_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "ai_api_key": "sk-...",
    "ai_base_url": "https://api.openai.com/v1",
    "ai_model": "gpt-4o",
    "ai_rpm": 15,
    "mcp_servers": {
      "1c-odata": {
        "transport": "stdio",
        "command": "python",
        "args": ["mcp_servers/odata_server.py"]
      },
      "memory": {
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@anthropic/mcp-server-memory"]
      },
      "fetch": {
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@anthropic/mcp-server-fetch"]
      },
      "filesystem": {
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@anthropic/mcp-server-filesystem", "--root", "."]
      },
      "thinking": {
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@anthropic/mcp-server-sequential-thinking"]
      }
    }
  }
}
```

---

## 🏆 Главная рекомендация

**Создать собственный `1c-odata` MCP-сервер** — это даст наибольший ROI. Сейчас вся OData-логика завязана на `bot.py` (400+ строк HTTP-запросов, парсинга метаданных, URL-кодирования). Вынос в отдельный MCP-сервер сделает его переиспользуемым компонентом, который можно подключать к любому ИИ-клиенту (Claude Desktop, Continue, Cursor, другой бот). Это превращает проект из «бота для 1С» в «платформу интеграции 1С + ИИ».

---

## Установка зависимостей

```bash
# Python MCP SDK (для собственного MCP-сервера)
pip install mcp

# Node.js MCP-серверы ставятся автоматически через npx,
# но можно установить глобально для скорости:
npm install -g @anthropic/mcp-server-memory @anthropic/mcp-server-fetch @anthropic/mcp-server-filesystem