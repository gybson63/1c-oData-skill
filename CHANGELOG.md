# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Workflow разработки: правило Cursor, CHANGELOG, conventional commits, pre-commit hooks, release script и GitHub Release workflow.
- MCP SearXNG для аналитика (`mcp-searxng`): Docker Compose, preflight/gating conf-doc, eval-скрипт блока 2.
- Документация: `CONTRIBUTING.md`, индекс `docs/README.md`, гайды getting-started, configuration, mcp-setup, searxng, project-structure, odata-troubleshooting.
- Гайд [`docs/email.md`](docs/email.md): настройка и использование email-интерфейса бота.

### Changed

- README: на поверхности только киллер-фичи; операционные детали перенесены в `docs/`.
- README: отдельный блок Email-интерфейс со ссылкой на `docs/email.md`.
- Аналитик: приоритет conf-doc над SearXNG; `max_tool_iterations` по умолчанию 12.

### Fixed

- `build_global_config`: передача `profile_config` для наследования shared MCP (conf-doc).
- Форматирование code blocks в `bot/README.md`, `docs/architecture.md`, `docs/ci-cd.md` под `ruff format` (CI).

### Removed

## [0.1.0] - 2026-06-08

### Added

- Telegram-бот с мультиагентной архитектурой (OData, formatter, analyst).
- Email-интерфейс, analytics-пайплайн OData, conf-doc контекст.
- CI: ruff, mypy, pytest; pre-commit hooks.
- Workflow разработки: feature-ветки, changelog, conventional commits, релизы через теги.
