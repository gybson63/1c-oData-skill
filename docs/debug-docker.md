# Отладка в Docker-контейнере

Инструкция по запуску и отладке бота в Docker-контейнере через VS Code.

## Файлы конфигурации

| Файл | Назначение |
|---|---|
| `Dockerfile.debug` | Образ с `debugpy` для удалённой отладки |
| `docker-compose.debug.yml` | Переопределения compose для отладки |
| `.vscode/launch.json` | Конфигурации запуска VS Code |
| `.vscode/tasks.json` | Задачи для управления Docker-контейнером |

## Предварительные требования

- Docker Desktop запущен
- Файл `env.json` существует в корне проекта (скопируйте из `env.example.json` и заполните)
- VS Code с расширением **Python** (отладчик `debugpy`)

## Способ 1: Полная отладка с брейкпоинтами

Контейнер запускается с `debugpy` и **ожидает** подключения отладчика VS Code. Брейкпоинты, пошаговое выполнение, просмотр переменных — всё работает.

### Шаги:

1. **Запустите debug-контейнер:**
   - `Ctrl+Shift+P` → `Tasks: Run Task` → `Docker: Start Debug Container`
   - Контейнер соберётся (если нужно) и запустится в фоне
   - Бот **не начнёт работу** — он ждёт подключения отладчика

2. **Подключите отладчик VS Code:**
   - Нажмите `F5`
   - Выберите **`Docker: Attach to Bot (debugpy)`**
   - Отладчик подключится к порту 5678, бот продолжит выполнение

3. **Работайте с брейкпоинтами:**
   - Расставьте точки останова в `bot/`, `bot_lib/`, `mcp_servers/`
   - Бот будет останавливаться на брейкпоинтах
   - Доступны: переменные, call stack, watch, debug console

4. **Остановите отладку:**
   - `Shift+F5` — отключить отладчик (бот продолжит работать)
   - `Tasks: Run Task` → `Docker: Stop Debug Container` — остановить контейнер

## Способ 2: Подключение к работающему боту

Контейнер запускается **без ожидания** отладчика. Бот работает, к нему можно подключиться в любой момент.

### Шаги:

1. **Запустите контейнер без ожидания:**
   - `Tasks: Run Task` → `Docker: Start Debug (no wait)`
   - Бот начнёт работу сразу

2. **Подключитесь при необходимости:**
   - `F5` → выберите **`Docker: Attach to Bot (wait=false)`**
   - Отладчик подключится, брейкпоинты начнут работать

3. **Отключитесь:**
   - `Shift+F5` — бот продолжит работу без отладчика

## Способ 3: Запуск тестов в контейнере

Проверка, что код корректно работает в Docker-окружении.

```
Tasks: Run Task → Tests: Run in Container
```

Выполнит `pytest tests/ -v` внутри debug-контейнера и выведет результат.

## Быстрый цикл разработки

Исходники (`bot/`, `bot_lib/`, `mcp_servers/`, `skills/`) **примонтированы как volumes** — изменения файлов видны в контейнере без пересборки образа.

```
1. Изменили код → Ctrl+S
2. Tasks: Run Task → Docker: Restart Debug Container
3. F5 → Docker: Attach to Bot (debugpy)
```

## Задачи VS Code (Ctrl+Shift+P → Tasks: Run Task)

| Задача | Описание |
|---|---|
| `Docker: Build Debug Image` | Собрать debug-образ |
| `Docker: Start Debug Container` | Запустить контейнер (ждёт отладчик) |
| `Docker: Start Debug (no wait)` | Запустить контейнер (не ждёт отладчик) |
| `Docker: Stop Debug Container` | Остановить и удалить контейнер |
| `Docker: Restart Debug Container` | Перезапустить (подхватит изменения исходников) |
| `Docker: View Bot Logs` | Просмотр логов контейнера в реальном времени |
| `Docker: Build Production Image` | Собрать production-образ (без debugpy) |
| `Tests: Run in Container` | Запустить pytest в контейнере |

## Конфигурации запуска VS Code (F5)

| Конфигурация | Описание |
|---|---|
| `Python: Current File` | Запустить текущий файл локально |
| `Bot: Local (python -m bot)` | Запустить бота локально без Docker |
| `Docker: Attach to Bot (debugpy)` | Подключиться к контейнеру, `justMyCode: false` |
| `Docker: Attach to Bot (wait=false)` | Подключиться к работающему боту, `justMyCode: true` |

## Маппинг путей

VS Code автоматически маппит пути:
- **Локальный:** `${workspaceFolder}` → **Контейнер:** `/app`

Это означает, что брейкпоинт в локальном файле `bot/bot.py:42` сработает на строке 42 в контейнере `/app/bot/bot.py`.

## Переменные окружения

| Переменная | Значение по умолчанию | Описание |
|---|---|---|
| `LOG_LEVEL` | `DEBUG` | Уровень логирования в debug-режиме |
| `WAIT_FOR_DEBUGGER` | `true` | `true` — ждать подключение VS Code, `false` — не ждать |

## Ручное управление через CLI

```bash
# Запуск debug-конфигурации
docker compose -f docker-compose.yml -f docker-compose.debug.yml up --build

# Запуск в фоне
docker compose -f docker-compose.yml -f docker-compose.debug.yml up -d --build

# Просмотр логов
docker compose -f docker-compose.yml -f docker-compose.debug.yml logs -f bot

# Остановка
docker compose -f docker-compose.yml -f docker-compose.debug.yml down

# Запуск тестов в контейнере
docker compose -f docker-compose.yml -f docker-compose.debug.yml run --rm bot python -m pytest tests/ -v
```

## Автоматические проверки

При каждом изменении кода проверки срабатывают автоматически на двух уровнях:

### 1. При сохранении файла (VS Code)

Настроено в `.vscode/settings.json`:
- **Ruff** автоматически исправляет ошибки линтера и форматирует код при сохранении (`Ctrl+S`)
- Добавляет завершающий перенос строки, удаляет лишние пробелы
- Требуется расширение VS Code: **Ruff** (`charliermarsh.ruff`)

### 2. Перед коммитом (pre-commit)

Настроено в `.pre-commit-config.yaml`, хук установлен в `.git/hooks/pre-commit`.

При `git commit` автоматически запускаются:

| Хук | Что проверяет |
|---|---|
| `ruff` | Линтер: ошибки, неиспользуемые импорты, стиль |
| `ruff-format` | Форматирование кода |
| `end-of-file-fixer` | Завершающий перенос строки (предотвращает W292) |
| `trailing-whitespace` | Лишние пробелы в конце строк |
| `check-added-large-files` | Файлы больше 500 КБ |
| `check-yaml` / `check-json` / `check-toml` | Синтаксис конфигов |
| `no-commit-to-branch` | Запрет прямого коммита в `main` / `develop` |

Если хук находит ошибки — он автоматически исправляет их и **отменяет коммит**. Нужно сделать `git add` исправленных файлов и повторить `git commit`.

### 3. В CI (GitHub Actions)

При push/PR запускается `.github/workflows/ci.yml`:
- `ruff check .` — линтер
- `mypy bot/ bot_lib/` — проверка типов
- `pytest` — тесты
- `docker build` — сборка образа

### Установка pre-commit (для нового разработчика)

```bash
pip install pre-commit
pre-commit install
```

### Ручной запуск всех проверок

```bash
# Ruff линтер + форматтер
python -m ruff check . --fix
python -m ruff format .

# Все pre-commit хуки
python -m pre_commit run --all-files
```

---

## Устранение неполадок

**Отладчик не подключается (порт 5678 занят):**
```bash
# Проверьте, что контейнер запущен
docker compose -f docker-compose.yml -f docker-compose.debug.yml ps

# Проверьте порт
netstat -an | findstr 5678
```

**Брейкпоинты не срабатывают (серые):**
- Убедитесь, что выбрана правильная конфигурация запуска `Docker: Attach to Bot`
- Проверьте маппинг путей в launch.json: `localRoot` → `${workspaceFolder}`, `remoteRoot` → `/app`

**Изменения кода не видны:**
- Исходники монтируются как read-only volumes — изменения на хосте видны сразу
- Если изменили `requirements.txt` — нужна пересборка: `Docker: Build Debug Image`
- Для применения изменений кода: `Docker: Restart Debug Container`

**Контейнер падает при старте:**
- Проверьте `env.json` — файл должен быть в корне проекта
- Посмотрите логи: `Docker: View Bot Logs`
