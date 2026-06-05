# Email-тестирование бота

Стратегия проверки бота через email-интерфейс: от быстрых интеграционных тестов (L1/L2) до полного E2E с живым AI и 1С (L3).

## Пирамида тестов

| Уровень | Файлы | Зависимости | CI |
|---------|-------|-------------|-----|
| **L1** | `tests/integration/test_email_chat.py` | мок агента | каждый PR |
| **L2** | `tests/integration/test_email_transport.py` | мок SMTP/IMAP | каждый PR |
| **L3** | `tests/integration/test_email_e2e.py` | GreenMail + AI + 1С | nightly / manual |

## Почтовый стенд

Для E2E используется **GreenMail** (IMAP + SMTP). MailHog не подходит: `EmailTransport` опрашивает входящие через IMAP.

```bash
docker compose -f docker-compose.test.yml up -d mail
```

Порты:

- `3025` — SMTP
- `3143` — IMAP
- `8025` — Web UI GreenMail

Пользователи (пароль `secret`):

- `bot@local.test` — ящик бота (IMAP/SMTP)
- `tester@local.test` — отправитель тестов

## Конфигурация

1. Скопируйте шаблон:

```bash
cp env.test.example.json env.test.json
```

2. Заполните `ai_api_key` и `agents.odata` (URL и учётные данные тестовой ИБ).

`env.test.json` в `.gitignore` — не коммитьте секреты.

Переменные окружения для E2E (опционально, перекрывают файл):

- `AI_API_KEY` / `OPENAI_API_KEY`
- `ODATA_URL`
- `TEST_SMTP_HOST`, `TEST_IMAP_HOST` (по умолчанию `localhost`)

## Запуск тестов

### Быстрые (CI на каждый PR)

```bash
pytest -m "not slow"
```

Только L1/L2:

```bash
pytest tests/integration/test_email_chat.py tests/integration/test_email_transport.py -v
```

### Slow E2E (живой AI)

```bash
docker compose -f docker-compose.test.yml up -d mail
pytest -m "slow" tests/integration/test_email_e2e.py -v --timeout=300
```

Один сценарий:

```bash
pytest -m "slow" tests/integration/test_email_e2e.py -k list_employees -v
```

При падении MIME-ответы сохраняются в `tests/artifacts/` (gitignored).

## Каталог сценариев

Реестр поведения: [`tests/scenarios/catalog.yaml`](../tests/scenarios/catalog.yaml).

Статусы:

- `planned` — запланировано, теста ещё нет
- `implemented` — есть L1 и/или L3 тест
- `flaky` — нестабильный (живой AI); завести issue со ссылкой на `id`
- `blocked` — нет тестовой ИБ или данных

Workflow:

1. Новая фича → строка в `catalog.yaml` со статусом `planned`
2. Реализация → L1 с моком → L3 E2E
3. Flaky прогон → `status: flaky` + issue в backlog

Загрузка в коде:

```python
from tests.helpers.scenario_catalog import load_catalog, scenarios_by_layer
```

## CI

- **Job `test`**: `pytest -m "not slow"` — L1/L2 + unit
- **Job `test-slow`**: `workflow_dispatch` или cron (понедельник 03:00 UTC)
  - поднимает GreenMail
  - требует secrets: `AI_API_KEY`, `ODATA_URL`, `ODATA_USER`, `ODATA_PASSWORD`
  - `continue-on-error: true` (нестабильность live AI)

Ручной запуск slow job: Actions → CI → Run workflow.

## Добавление нового сценария

1. Добавьте запись в `tests/scenarios/catalog.yaml`
2. Для L1 — расширьте `test_email_chat.py` (мок агента)
3. Для L3 — добавьте тест в `test_email_e2e.py` с `@pytest.mark.slow`
4. Используйте структурные assert (`assert_no_error`, regex), не точный текст LLM
5. Обновите `test_catalog_implemented_e2e_ids_exist` при статусе `implemented`

## Harness API

Модуль [`tests/helpers/email_harness.py`](../tests/helpers/email_harness.py):

- `send_email()` — SMTP
- `wait_for_reply()` — IMAP или MailHog HTTP API
- `parse_reply_mime()` — разбор ответа
- `unique_subject()` — изоляция тестов
- `save_artifact()` — отладка при падении
