# Быстрый старт

Минимальный путь от клона репозитория до работающего Telegram-бота.

---

## Требования

| Компонент | Версия / примечание |
|-----------|---------------------|
| **1С:Предприятие** | 8.3.6+, OData-интерфейс опубликован на веб-сервере |
| **Python** | 3.10+ |
| **Node.js + npm** | Для MCP через `npx` (odata, mcp-searxng) — опционально |
| **PowerShell** | Для сборки EPF на Windows |
| **1c-conf-doc** | HTTP API на `:8050` — рекомендуется для точных OData-запросов |
| **SearXNG** | Docker — опционально, для веб-поиска аналитика ([searxng.md](searxng.md)) |

---

## Установка

```bash
git clone <repo-url> 1c-oData-skill
cd 1c-oData-skill
pip install -r requirements.txt
```

Dev-окружение (для разработки):

```bash
pip install -e ".[dev]"
pre-commit install
pre-commit install --hook-type commit-msg
```

---

## Конфигурация

```bash
cp env.example.json env.json
```

Заполните в `env.json`:

- `telegram_token` — токен бота от @BotFather
- `ai_api_key`, `ai_base_url`, `ai_model` — провайдер AI
- `agents.odata.odata_url`, `odata_user`, `odata_password` — доступ к 1С OData
- `mcp_servers.conf-doc` — URL и имя конфигурации (если используете conf-doc)

Подробная схема: [`configuration.md`](configuration.md).

---

## Чеклист запуска

### 1. OData доступен

```bash
bash examples/check-availability.sh
```

Или см. [`full-guide.md`](full-guide.md) — шаг 1.

Если объекты не опубликованы — [`processing/README.md`](../processing/README.md) (обработка EnableODataInterface).

### 2. conf-doc (рекомендуется)

```bash
curl http://localhost:8050/health
curl http://localhost:8050/configurations
```

Имя для `CONF_DOC_CONFIGURATION` — поле `name` из `/configurations` (например `ЗарплатаИУправлениеПерсоналомКОРП`).

MCP для Cursor: [`mcp-setup.md`](mcp-setup.md).

### 3. Запуск бота

```bash
python -m bot
# Telegram + email одновременно:
python -m bot --transport both
```

Email только: `--transport email`. Инструкция: [`email.md`](email.md).

### 4. SearXNG (опционально)

```bash
docker compose -f docker-compose.searxng.yml up -d
python scripts/enable_searxng_mcp.py
python scripts/test_searxng_mcp.py
```

Детали: [`searxng.md`](searxng.md).

---

## Что дальше

| Задача | Документ |
|--------|----------|
| Настройка env.json | [`configuration.md`](configuration.md) |
| **Email-интерфейс** | **[`email.md`](email.md)** |
| MCP в Cursor | [`mcp-setup.md`](mcp-setup.md) |
| SearXNG для аналитика | [`searxng.md`](searxng.md) |
| Пользователь бота (Telegram) | [`user-guide.md`](user-guide.md) |
| OData + conf-doc workflow | [`full-guide.md`](full-guide.md) |
| Архитектура бота | [`architecture.md`](architecture.md), [`bot/README.md`](../bot/README.md) |
