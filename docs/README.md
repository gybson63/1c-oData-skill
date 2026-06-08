# Документация проекта 1c-oData-skill

Индекс по аудиториям. Точка входа для новых пользователей: [`getting-started.md`](getting-started.md).

---

## Пользователь (Telegram / email)

| Документ | Описание |
|----------|----------|
| [`user-guide.md`](user-guide.md) | Руководство пользователя: вопросы, команды, токены, FAQ |

---

## Быстрый старт и настройка

| Документ | Описание |
|----------|----------|
| [`getting-started.md`](getting-started.md) | Установка, требования, чеклист запуска |
| [`configuration.md`](configuration.md) | Канонический справочник env.json |
| [`mcp-setup.md`](mcp-setup.md) | MCP для Cursor: odata, conf-doc |
| [`full-guide.md`](full-guide.md) | Технический workflow 1cconfinfo + OData (curl) |
| [`../processing/README.md`](../processing/README.md) | Обработка EnableODataInterface (публикация OData) |

---

## SearXNG (веб-поиск аналитика)

| Документ | Описание |
|----------|----------|
| **[`searxng.md`](searxng.md)** | Docker, MCP, gating conf-doc, env.json, Cursor, отладка, eval |

---

## Разработчик бота

| Документ | Описание |
|----------|----------|
| [`../bot/README.md`](../bot/README.md) | Агенты, logging, metrics, запуск |
| [`architecture.md`](architecture.md) | Архитектура: pipeline, MCP, skills, метрики |
| [`project-structure.md`](project-structure.md) | Дерево каталогов |
| [`email-testing.md`](email-testing.md) | Тестирование email-интерфейса (L1/L2/L3) |
| [`debug-docker.md`](debug-docker.md) | Отладка в Docker (debugpy) |

---

## Ops / CI

| Документ | Описание |
|----------|----------|
| [`ci-cd.md`](ci-cd.md) | CI pipeline: ruff, mypy, pytest, Docker |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | Ветки, commits, pre-commit, релизы |

---

## AI-агент (Cursor / Cline)

| Документ | Описание |
|----------|----------|
| [`../skills/odata/SKILL.md`](../skills/odata/SKILL.md) | OData-запросы через MCP |
| [`../skills/conf-doc/SKILL.md`](../skills/conf-doc/SKILL.md) | conf-doc search API |
| [`../skills/1cconfinfo/SKILL.md`](../skills/1cconfinfo/SKILL.md) | Анализ структуры конфигурации |
| [`../skills/analyst/SKILL.md`](../skills/analyst/SKILL.md) | Аналитик метаданных |
| [`../skills/analyst-mcp/SKILL.md`](../skills/analyst-mcp/SKILL.md) | MCP-каталог аналитика → [searxng.md](searxng.md) |
| [`../skills/analyst-conf-doc/SKILL.md`](../skills/analyst-conf-doc/SKILL.md) | conf-doc для аналитика |
| [`../skills/analyst-domain/SKILL.md`](../skills/analyst-domain/SKILL.md) | Profiles, decision trees |
| [`../skills/analyst/profiles/zup-korp.md`](../skills/analyst/profiles/zup-korp.md) | Domain profile ЗУП КОРП |
| [`../.cursor/rules/analyst.mdc`](../.cursor/rules/analyst.mdc) | Cursor rule: analyst workflow |
| [`../.cursor/rules/conf-doc-search.mdc`](../.cursor/rules/conf-doc-search.mdc) | Cursor rule: conf-doc перед OData |

---

## Оценка conf-doc и analyst

| Документ | Описание |
|----------|----------|
| [`conf-doc-evaluation-checklist.md`](conf-doc-evaluation-checklist.md) | Чеклист: 10 вопросов ЗУП |
| [`case-n8-vacation-balance.md`](case-n8-vacation-balance.md) | Кейс №8: остатки отпусков |

---

## Справочники и troubleshooting

| Документ | Описание |
|----------|----------|
| [`odata.md`](odata.md) | Справочник OData REST (платформа 1С) |
| [`odata-troubleshooting.md`](odata-troubleshooting.md) | URL-кодирование фильтров, типичные ошибки |
| [`1c-value-tree-in-forms.md`](1c-value-tree-in-forms.md) | Деревья значений в формах 1С (tangential) |

---

## Прочее

| Документ | Описание |
|----------|----------|
| [`../README.md`](../README.md) | Обзор проекта и киллер-фичи |
| [`../CHANGELOG.md`](../CHANGELOG.md) | История изменений |
| [`../.github/pull_request_template.md`](../.github/pull_request_template.md) | Шаблон PR |
