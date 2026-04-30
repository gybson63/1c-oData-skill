Публикация на ![Infostart](https://infostart.ru/bitrix/templates/sandbox_empty/assets/tpl/abo/img/logo.svg) https://infostart.ru/1c/articles/2632839/

# 1c-oData-skill

Проект для работы с 1С:Предприятие через стандартный OData-интерфейс и разработки внешних обработок.

## Скилл: OData-запросы к данным 1С

Основной скилл проекта — [`skills/odata/`](skills/odata/SKILL.md). Позволяет запрашивать данные любых объектов 1С через REST OData без написания кода.

### Быстрый старт

Credentials хранятся в `env.json` (не в git):

```json
{
  "default": {
    "odata_url": "http://localhost/your_base/odata/standard.odata",
    "odata_user": "Администратор",
    "odata_password": "пароль"
  }
}
```

Загрузить переменные и выполнить запрос:

```bash
ODATA_URL=$(node -e "const d=require('./env.json').default; process.stdout.write(d.odata_url)")

ODATA_AUTH=$(node -e "const d=require('./env.json').default; process.stdout.write(Buffer.from(d.odata_user+':'+d.odata_password).toString('base64'))")

# URL-кодирование кириллицы
ENCODED=$(node -e "process.stdout.write(encodeURIComponent('Контрагенты'))")

curl -s -H "Authorization: Basic $ODATA_AUTH" -H "Accept: application/json" \
  "$ODATA_URL/Catalog_${ENCODED}?\$top=10&\$format=json"
```

> **Важно:** `curl -u "user:pass"` не работает с кириллицей на Windows — используй только заголовок `Authorization: Basic`.
> PowerShell не подходит — ломает base64 при кириллических паролях.

### Типы объектов в URL

| Тип 1С | Префикс в OData |
|--------|-----------------|
| Справочник | `Catalog_` |
| Документ | `Document_` |
| РегистрСведений | `InformationRegister_` |
| РегистрНакопления | `AccumulationRegister_` |
| РегистрРасчета | `CalculationRegister_` |
| ПланВидовРасчета | `ChartOfCalculationTypes_` |
| Перечисление | `Enum_` |

Подробнее — в [`skills/odata/SKILL.md`](skills/odata/SKILL.md).

---

## Внешняя обработка: включение объектов в OData

Обработка [`processing/EnableODataInterface.epf`](processing/) позволяет выбрать конкретные объекты конфигурации для публикации через OData — без Конфигуратора, прямо из режима Предприятия.

### Форма обработки

```
┌─────────────────────────────────────────────────────────────┐
│  [Применить]  [Выбрать все]  [Снять все]                    │
│  Текущий состав загружен. Опубликовано объектов: 342.       │
├─────────────────────────────────────────────────────────────┤
│  ▼ Справочники                                              │
│    ☑  Контрагенты                                           │
│    ☑  Сотрудники                                            │
│    ☐  ФизическиеЛица                                        │
│  ▼ Документы                                                │
│    ☑  НачислениеЗарплаты                                    │
│    ☐  ПриемНаРаботу                                         │
│  ▶ Регистры сведений                                        │
│  ▶ Регистры накопления                                      │
│  ...                                                        │
└─────────────────────────────────────────────────────────────┘
```

При открытии обработка читает текущий состав OData и расставляет флажки. При нажатии **«Применить»** — сохраняет только отмеченные объекты.

### Сборка EPF из исходников

```powershell
powershell.exe -NoProfile -File .claude/skills/epf-build/scripts/epf-build.ps1 `
    -SourceFile "processing/EnableODataInterface.xml" `
    -OutputFile "processing/EnableODataInterface.epf"
```

Если база занята конфигуратором — скрипт автоматически создаст временную базу-заглушку.

---

## Telegram-бот для запросов к 1С

Бот принимает вопросы на русском языке и возвращает данные из базы 1С — без написания запросов вручную.

**Возможности:**
- Подбор нужного объекта по смыслу вопроса (`Catalog_ПодразделенияОрганизаций`, `Document_РеализацияТоваровУслуг` и т.д.)
- Подсчёт записей (`Сколько сотрудников в базе?` → число через `/$count`)
- Инструменты для агента: по запросу агент получает справку по синтаксису OData или список полей объекта — не перегружая системный промпт заранее
- Файл `bot/config_hint.md` — описание терминологии вашей конфигурации (ЗУП, ERP, УТ и т.д.)

Подробное описание, настройка и конфигурация — в [`bot/README.md`](bot/README.md).

### Быстрый запуск

```bash
pip install python-telegram-bot openai

# Добавить в env.json поля telegram_token, ai_api_key, ai_base_url, ai_model
# (odata_url, odata_user, odata_password уже должны быть)

python bot/bot.py
```

---

## Документация

| Файл | Описание |
|------|----------|
| [`skills/odata/SKILL.md`](skills/odata/SKILL.md) | OData-запросы: параметры, фильтры, примеры |
| [`skills/1cconfinfo/SKILL.md`](skills/1cconfinfo/SKILL.md) | Анализ XML-выгрузки конфигурации 1С |
| [`docs/1c-value-tree-in-forms.md`](docs/1c-value-tree-in-forms.md) | Деревья значений в управляемых формах 1С |
| [`docs/mcp-recommendations.md`](docs/mcp-recommendations.md) | Рекомендации MCP-серверов для расширения проекта |
| [`processing/README.md`](processing/README.md) | Описание обработки EnableODataInterface |

---

## Структура проекта

```
bot/
  bot.py              — Telegram-бот
  master_prompt.md    — промпт форматирования ответов (шаг 2)
  config_hint.md      — терминология конфигурации 1С (шаг 1)
  README.md           — описание бота
skills/
  odata/              — скилл OData-запросов
  1cconfinfo/         — скилл анализа конфигурации
processing/
  EnableODataInterface.epf        — собранная обработка
  EnableODataInterface.xml        — метаданные
  EnableODataInterface/           — XML-исходники
docs/
  1c-value-tree-in-forms.md       — гайд по деревьям в формах 1С
env.json                          — credentials (не в git)
env.example.json                  — пример credentials
```

## Требования

- 1С:Предприятие 8.3.6+
- База опубликована на веб-сервере с включённым OData
- Node.js (для чтения env.json и кодирования запросов)
- PowerShell (встроен в Windows — для сборки EPF)
