Публикация на ![Infostart](https://infostart.ru/bitrix/templates/sandbox_empty/assets/tpl/abo/img/logo.svg) https://infostart.ru/1c/articles/2686030

# 1c-oData-skill

Работа с 1С:Предприятие через стандартный OData: скиллы для AI-агентов, Telegram- и email-бот с мультиагентной архитектурой, обработка публикации объектов в OData.

---

## Возможности

### Данные 1С на естественном языке

- Telegram- и email-бот: вопрос на русском → данные из базы 1С без ручных запросов
- Мультиагентный AI-пайплайн: **аналитик метаданных** (опционально) → **OData** (формирование запроса) → **форматтер** (оформление ответа)
- Подбор сущности по смыслу вопроса, подсчёт записей (`/$count`), раскрытие ссылок через `$expand`
- Учёт токенов и стоимости по сессии (`/tokens`)

Подробнее: [`docs/user-guide.md`](docs/user-guide.md), [`bot/README.md`](bot/README.md).

### conf-doc: поиск по метаданным

- Семантический keyword-поиск объектов и реквизитов конфигурации до OData-запроса
- Обогащение Step 1 OData-агента контекстом из метаданных
- MCP `1c-conf-doc` для Cursor и бота

Подробнее: [`skills/conf-doc/SKILL.md`](skills/conf-doc/SKILL.md), [`docs/mcp-setup.md`](docs/mcp-setup.md).

### Аналитик метаданных

- Первый этап пайплайна (pre-step): conf-doc + profile → MetadataBrief для OData Step 1
- Отдельный режим: `/analyze` в Telegram, `[analyze]` в email — без запроса данных
- Domain profiles (ЗУП КОРП и др.): decision trees, anti-patterns

Подробнее: [`skills/analyst/SKILL.md`](skills/analyst/SKILL.md).

### SearXNG: веб-поиск для аналитика

- Локальный self-hosted metasearch через MCP `mcp-searxng`
- Fallback, когда conf-doc и profile не дают достаточно контекста (имена реквизитов, OData-поведение, внешняя документация 1С)
- Gating: SearXNG только **после** успешного conf-doc search
- Инструменты: `searxng_web_search`, `web_url_read`

Подробнее: **[`docs/searxng.md`](docs/searxng.md)**.

### Скиллы для AI-агентов

- [`skills/odata/`](skills/odata/SKILL.md) — OData-запросы через MCP
- [`skills/1cconfinfo/`](skills/1cconfinfo/SKILL.md) — структура конфигурации
- [`skills/conf-doc/`](skills/conf-doc/SKILL.md) — семантический поиск метаданных
- [`skills/analyst-*`](skills/analyst/SKILL.md) — аналитик, domain profiles, MCP-каталог

Workflow: `conf_doc_search` → `fetch` (OData). См. [`docs/full-guide.md`](docs/full-guide.md).

### EnableODataInterface

Внешняя обработка для публикации объектов в OData из режима Предприятия — без Конфигуратора, с выбором конкретных справочников, документов и регистров.

Подробнее: [`processing/README.md`](processing/README.md).

---

## Быстрый старт

```bash
pip install -r requirements.txt
cp env.example.json env.json   # заполните токены и OData
python -m bot
```

Полный чеклист (OData, conf-doc, SearXNG): [`docs/getting-started.md`](docs/getting-started.md).

---

## Документация

| Аудитория | Куда идти |
|-----------|-----------|
| Пользователь бота | [`docs/user-guide.md`](docs/user-guide.md) |
| Установка и настройка | [`docs/getting-started.md`](docs/getting-started.md), [`docs/configuration.md`](docs/configuration.md) |
| MCP в Cursor | [`docs/mcp-setup.md`](docs/mcp-setup.md) |
| **SearXNG** | **[`docs/searxng.md`](docs/searxng.md)** |
| Разработчик бота | [`bot/README.md`](bot/README.md), [`docs/architecture.md`](docs/architecture.md) |
| Участие в разработке | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| Полный индекс | [`docs/README.md`](docs/README.md) |

---

## Участие в разработке

Feature-ветки от `main`, Conventional Commits, PR-only merge, pre-commit (ruff, mypy). См. [`CONTRIBUTING.md`](CONTRIBUTING.md) и [`docs/ci-cd.md`](docs/ci-cd.md).
