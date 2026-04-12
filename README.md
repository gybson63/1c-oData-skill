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
ODATA_URL=$(python -c "import json; d=json.load(open('env.json', encoding='utf-8')); print(d['default']['odata_url'])")
ODATA_AUTH=$(python -c "import base64,json; d=json.load(open('env.json', encoding='utf-8')); u=d['default']['odata_user']; p=d['default']['odata_password']; print(base64.b64encode(f'{u}:{p}'.encode()).decode())")

# Получить данные справочника
ENCODED=$(python -c "from urllib.parse import quote; print(quote('Контрагенты'))")
curl -s -H "Authorization: Basic $ODATA_AUTH" -H "Accept: application/json" \
  "$ODATA_URL/Catalog_${ENCODED}?\$top=10&\$format=json"
```

> **Важно:** `curl -u "user:pass"` не работает с кириллицей на Windows — используй только заголовок `Authorization: Basic`.

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

## Документация

| Файл | Описание |
|------|----------|
| [`skills/odata/SKILL.md`](skills/odata/SKILL.md) | OData-запросы: параметры, фильтры, примеры |
| [`skills/1cconfinfo/SKILL.md`](skills/1cconfinfo/SKILL.md) | Анализ XML-выгрузки конфигурации 1С |
| [`docs/1c-value-tree-in-forms.md`](docs/1c-value-tree-in-forms.md) | Деревья значений в управляемых формах 1С |
| [`processing/README.md`](processing/README.md) | Описание обработки EnableODataInterface |

---

## Структура проекта

```
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
- Python 3 (для формирования заголовка Authorization)
- PowerShell (для сборки EPF)
