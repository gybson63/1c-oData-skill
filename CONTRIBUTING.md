# Участие в разработке

## Ветки

Базовая ветка — **`main`**. Ветка `master` не используется.

Feature-ветки от `main`:

- `feature/...` — новая функциональность
- `fix/...` — исправления
- `docs/...` — документация
- `chore/...` — инфраструктура

Merge только через **Pull Request** в `main`.

---

## Коммиты

Формат [Conventional Commits](https://www.conventionalcommits.org/ru/):

```text
feat: добавить поддержку email-интерфейса
fix: исправить gating SearXNG до conf-doc
docs: обновить getting-started
```

Перед коммитом обновляйте [`CHANGELOG.md`](CHANGELOG.md), секция `[Unreleased]`.

---

## Проверки перед PR

```bash
ruff check .
ruff format .
mypy bot/ bot_lib/ mcp_servers/
pytest -m "not slow"
```

### Pre-commit

```bash
pip install -e ".[dev]"
pre-commit install
pre-commit install --hook-type commit-msg
```

Подробности CI, coverage, slow tests: [`docs/ci-cd.md`](docs/ci-cd.md).

Правила workflow в Cursor: [`.cursor/rules/development-workflow.mdc`](.cursor/rules/development-workflow.mdc).

---

## Релизы

SemVer. Подготовка:

```bash
python scripts/release.py prepare X.Y.Z
```

Тег `vX.Y.Z` → GitHub Release (workflow `.github/workflows/release.yml`).

---

## Pull Request

Используйте шаблон [`.github/pull_request_template.md`](.github/pull_request_template.md): Summary, ссылка на CHANGELOG, test plan.
