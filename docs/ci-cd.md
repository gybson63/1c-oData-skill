# CI/CD Pipeline — инструкция

> Автоматическая проверка кода при каждом push и Pull Request.

---

## Что делает пайплайн

Файл `.github/workflows/ci.yml` запускает джобы:

```
lint  →  test  →  build-docker
         ↑
    test-slow (nightly / manual, optional)
```

### 1. Lint (`ruff` + `mypy`)

| Инструмент | Что проверяет | Блокирует PR? |
|---|---|---|
| **Ruff check** | стиль кода, неиспользуемые импорты, ошибки | ✅ Да |
| **Ruff format** | единообразное форматирование (`--check`) | ✅ Да |
| **Mypy** | типы, сигнатуры функций | ⚠️ Нет (`continue-on-error`) |

### 2. Test (`pytest` + coverage)

- Запускает тесты из `tests/`, **исключая** `@pytest.mark.slow`
- Команда: `pytest -m "not slow" --cov=bot --cov=bot_lib`
- Включает L1/L2 email-интеграцию (`tests/integration/`)
- Генерирует отчёт покрытия в `coverage.xml`
- Опционально загружает покрытие в **Codecov** (нужен `CODECOV_TOKEN`)

### 2b. Slow tests (`test-slow`, email E2E)

- Запуск: **workflow_dispatch** или cron (понедельник 03:00 UTC)
- Поднимает GreenMail (`docker-compose.test.yml`)
- Прогоняет `pytest -m "slow" tests/integration/test_email_e2e.py`
- Secrets: `AI_API_KEY`, `ODATA_URL`, `ODATA_USER`, `ODATA_PASSWORD`
- `continue-on-error: true` — не блокирует merge (нестабильность live AI)
- Подробнее: [email-testing.md](email-testing.md)

### 3. Build Docker

- Собирает Docker-образ `1c-odata-skill:ci`
- Запускает smoke-test: `python -c "import bot"`
- Срабатывает **только на push** (не на PR)

---

## Когда запускается

| Событие | lint | test | build-docker |
|---|---|---|---|
| Push в `main` | ✅ | ✅ | ✅ |
| Push в `develop` | ✅ | ✅ | ✅ |
| PR в `main` | ✅ | ✅ | ❌ |
| Schedule / manual | — | slow E2E | — |

Повторные запуски того же ветки/PR автоматически отменяются (`concurrency`).

---

## Как запустить локально

### Линтинг

```bash
# Установить инструменты (один раз)
pip install ruff mypy

# Ruff — проверка стиля
ruff check .

# Ruff — автоисправление
ruff check --fix .

# Ruff — проверка форматирования (как в CI)
ruff format --check .

# Ruff — применить форматирование
ruff format .

# Mypy — проверка типов
mypy bot/ bot_lib/ mcp_servers/ --ignore-missing-imports
```

### Pre-commit (перед каждым коммитом)

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files   # ручной прогон
```

### Тесты

```bash
# Быстрые тесты (как в CI)
pytest -m "not slow"

# Slow email E2E (нужен GreenMail + env.test.json)
docker compose -f docker-compose.test.yml up -d mail
pytest -m "slow" tests/integration/test_email_e2e.py -v

# С покрытием
pytest -m "not slow" --cov=bot --cov=bot_lib --cov-report=term-missing

# Только один файл
pytest tests/test_config.py -v

# С генерацией XML для Codecov
pytest --cov=bot --cov=bot_lib --cov-report=xml
```

---

## Настройка Codecov (опционально)

1. Зарегистрироваться на [codecov.io](https://codecov.io)
2. Подключить репозиторий
3. Скопировать токен → добавить в GitHub:
   - **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `CODECOV_TOKEN`
   - Value: `<токен из Codecov>`

Без токена пайплайн всё равно работает — просто пропускается загрузка покрытия.

---

## Конфигурация инструментов — `pyproject.toml`

Все настройки集中在 одном файле:

```toml
[tool.ruff]           # Линтер: target Python 3.12, line-length 120
[tool.mypy]           # Типы: ignore-missing-imports, warn_return_any
[tool.pytest.ini_options]  # Тесты: testpaths, asyncio_mode, markers
[tool.coverage.run]   # Покрытие: source dirs, omit patterns
[tool.coverage.report] # Отчёт: exclude_lines, fail_under
```

### Изменить порог покрытия

```toml
[tool.coverage.report]
fail_under = 30   # тесты упадут, если покрытие < 30%
```

Сейчас стоит `0` — покрытие не блокирует. По мере написания тестов повышайте порог.

### Добавить правило Ruff

```toml
[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP", "B"]  # B = bugbear
```

Список правил: https://docs.astral.sh/ruff/rules/

---

## Поддержка

### Добавить новый шаг в пайплайн

Отредактируйте `.github/workflows/ci.yml`:

```yaml
jobs:
  # ... существующие джобы ...

  my-new-job:
    name: My New Check
    runs-on: ubuntu-latest
    needs: test    # или lint, или без needs для параллельного запуска
    steps:
      - uses: actions/checkout@v4
      - run: echo "Hello"
```

### Изменить версию Python

В `ci.yml` и `pyproject.toml` — синхронно:

```yaml
# ci.yml
- uses: actions/setup-python@v5
  with:
    python-version: "3.13"
```

```toml
# pyproject.toml
[tool.ruff]
target-version = "py313"

[tool.mypy]
python_version = "3.13"
```

### Обновить actions (раз в полгода)

```yaml
uses: actions/checkout@v5      # было v4
uses: actions/setup-python@v6  # было v5
uses: codecov/codecov-action@v5
```

### Добавить маркер для медленных тестов

В `pyproject.toml` уже есть маркеры:

```toml
[tool.pytest.ini_options]
markers = [
    "slow: медленные тесты (network, AI)",
    "integration: интеграционные тесты",
]
```

Использование в тесте:

```python
@pytest.mark.slow
def test_real_odata_request():
    ...
```

Пропустить медленные:

```bash
pytest -m "not slow"
```

---

## Структура файлов

```
.github/
  workflows/
    ci.yml            ← CI/CD пайплайн

pyproject.toml        ← Конфигурация ruff, mypy, pytest, coverage
pytest.ini            ← УДАЛЁН (перенесено в pyproject.toml)
requirements.txt      ← Зависимости (включая pytest, ruff, mypy)
tests/
  conftest.py         ← Общие фикстуры
  test_config.py      ← Тесты конфигурации
  test_metadata_parser.py  ← Тесты парсинга метаданных
  test_odata_client.py     ← Тесты OData-клиента
```

---

## Частые проблемы

### ❌ Ruff падает на CI, но проходит локально

Версии могут отличаться. Зафиксируйте:

```bash
pip install ruff==0.11.0    # точная версия
```

### ❌ Mypy выдаёт ошибки на CI

`mypy` запущен с `continue-on-error: true` — ошибки видны в логе, но не блокируют. Чтобы исправить:

```bash
mypy bot/ bot_lib/ --ignore-missing-imports
```

Исправляйте ошибки постепенно. Когда все устранены — уберите `continue-on-error` из `ci.yml`.

### ❌ Тесты падают из-за покрытия

Если `fail_under > 0` и покрытие ниже порога — тесты упадут. Временно уменьшите порог или добавьте тесты.
