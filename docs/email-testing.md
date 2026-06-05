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
- `8025` — GreenMail API (OpenAPI, не веб-почта)

Пользователи (пароль `secret`):

- `bot@local.test` — ящик бота (IMAP/SMTP), логин IMAP/SMTP: `bot`
- `tester@local.test` — отправитель тестов, логин IMAP: `tester`

GreenMail в Docker слушает `0.0.0.0` (см. `GREENMAIL_OPTS` в `docker-compose.test.yml`).

## Конфигурация

1. Скопируйте шаблон:

```bash
cp env.test.example.json env.test.json
```

2. Заполните `ai_api_key` и `agents.odata` (URL и учётные данные тестовой ИБ).

3. Для сценария `email-max-fetch-cap` задайте `email.max_fetch_records` (например `30`) — иначе проверка лимита выгрузки не сработает.

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
pytest -m "slow" tests/integration/test_email_e2e.py -v --timeout=700
```

Один сценарий:

```bash
pytest -m "slow" tests/integration/test_email_e2e.py -k list_employees -v
```

При падении MIME-ответы сохраняются в `tests/artifacts/` (gitignored).

## Сценарии ЗУП

E2E-вопросы привязаны к типовым задачам **1С:ЗУП 3.1** (по материалам о настройке типовых отчётов: [Хэндисофт](https://handy-soft.ru/blog/nastrojka-tipovyh-otchyotov-v-1s-zup/), [1С-ИЖТИСИ](https://xn--1--rlchba2deh.xn--p1ai/%D1%81%D1%82%D0%B0%D1%82%D1%8C%D0%B8/%D0%BE%D1%82%D1%87%D0%B5%D1%82%D1%8B_%D0%B7%D1%83%D0%BF)):

| Задача в ЗУП | Типовой отчёт / раздел | Сценарий каталога | OData-объект |
|--------------|------------------------|-------------------|--------------|
| Список работающих с должностью и подразделением | Штатные сотрудники | `email-list-employees` | `Catalog_Сотрудники` |
| Численность штата | Штатные сотрудники | `email-count-employees` | `Catalog_Сотрудники` |
| Список юрлиц | Параметр «Организация» | `email-zup-organizations` | `Catalog_Организации` |
| Отбор по подразделению | Отбор в кадровых отчётах | `email-zup-departments` | `Catalog_ПодразделенияОрганизаций` |
| Должности / штатное расписание | Штатное расписание | `email-zup-positions` | `Catalog_Должности` |
| Паспортные и личные данные | Личные данные сотрудников | `email-zup-physical-persons` | `Catalog_ФизическиеЛица` |
| Расшифровка ссылок | Настройка полей отчёта | `email-reference-labels` | ссылочные поля |
| Расширенная выгрузка | Личные данные / штатные | `email-long-report` | `Catalog_Сотрудники` |
| Сверка списка из Excel | Внешние данные | `email-inbound-csv` | вложение CSV |
| Уточнение отбора в переписке | Вариант отчёта | `email-thread-followup` | контекст цепочки |

Сценарии `blocked` (зарплата, остатки отпусков) требуют регистров расчёта — зависят от публикации OData в конкретной ИБ.

Просмотр тестовой почты: GreenMail **не имеет веб-почтовика** — только API на `:8025`. Письма смотрите через IMAP-клиент (Thunderbird): логин `bot` / `tester`, пароль `secret`, порт `3143`, без шифрования.

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
