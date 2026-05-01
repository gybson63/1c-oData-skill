---
name: odata
description: Получение данных из 1С:Предприятие через стандартный OData-интерфейс
---

# OData — запросы к данным 1С через MCP fetch

## Описание

Скилл для получения данных из 1С:Предприятие через стандартный REST OData-интерфейс.
1С реализует **OData версии 3.0** (протокол `odata/standard.odata`).
Работает с любой конфигурацией 1С, где опубликована база через веб-сервер.

## MCP-инструмент fetch

Для запросов к 1С OData используется MCP-инструмент **`fetch`** (пакет `@modelcontextprotocol/server-fetch`).

Все запросы выполняются через вызов:

```
fetch(
  url="полный_URL_запроса",
  method="GET",
  headers={
    "Authorization": "Basic <base64>",
    "Accept": "application/json"
  }
)
```

## Настройка credentials

Credentials хранятся в файле `env.json` (вне репозитория, в `.gitignore`):

```json
{
  "default": {
    "odata_url": "http://localhost/your_base/odata/standard.odata",
    "odata_user": "Администратор",
    "odata_password": "пароль"
  }
}
```

## Подготовка авторизации

Заголовок `Authorization` формируется один раз — Base64-кодировка строки `логин:пароль`:

```
"Authorization": "Basic " + Base64Encode(odata_user + ":" + odata_password)
```

> **Важно:** Используйте **только** заголовок `Authorization: Basic`.
> Кодировка кириллических паролей корректно работает через Base64 в UTF-8.

## Базовый URL

Базовый URL берётся из `env.json` → `odata_url`, например:
`http://localhost/your_base/odata/standard.odata`

Все пути ресурсов добавляются после него через `/`.

## Проверить доступные сущности

```
fetch(
  url="http://localhost/your_base/odata/standard.odata/",
  method="GET",
  headers={"Authorization": "Basic <base64>", "Accept": "application/json"}
)
```

## Типы объектов 1С в OData

Каждый тип объекта 1С имеет свой префикс в URL-имени ресурса:

| Тип 1С | Префикс OData | Пример ресурса |
|--------|---------------|----------------|
| Справочник | `Catalog_` | `Catalog_Сотрудники` |
| Документ | `Document_` | `Document_Отпуск` |
| Журнал документов | `DocumentJournal_` | `DocumentJournal_ДокументыОплата` |
| План видов характеристик | `ChartOfCharacteristicTypes_` | `ChartOfCharacteristicTypes_ВидыСубконто` |
| План счетов | `ChartOfAccounts_` | `ChartOfAccounts_Основной` |
| План видов расчета | `ChartOfCalculationTypes_` | `ChartOfCalculationTypes_Начисления` |
| План обмена | `ExchangePlan_` | `ExchangePlan_ОбменСБухгалтерией` |
| Константа | `Constant_` | `Constant_АдресПубликации` |
| Регистр сведений | `InformationRegister_` | `InformationRegister_Цены` |
| Регистр накопления | `AccumulationRegister_` | `AccumulationRegister_Продажи` |
| Регистр расчёта | `CalculationRegister_` | `CalculationRegister_Начисления` |
| Регистр бухгалтерии | `AccountingRegister_` | `AccountingRegister_Хозрасчетный` |
| Бизнес-процесс | `BusinessProcess_` | `BusinessProcess_ЗаявкаНаСогласование` |
| Задача | `Task_` | `Task_ЗадачаИсполнителя` |
| Перечисление | `Enum_` | `Enum_ПолФизическогоЛица` |

## Суффиксы ресурсов регистров

Регистры имеют несколько вариантов ресурса в зависимости от типа выборки:

### Регистры сведений

| Суффикс | Назначение | Пример |
|---------|-----------|--------|
| `_RecordType` | Записи регистра | `InformationRegister_Цены_RecordType` |
| `_СрезПоследних` | Срез последних (только периодические) | `InformationRegister_Цены_СрезПоследних` |

### Регистры накопления / расчёта / бухгалтерии

| Суффикс | Назначение | Пример |
|---------|-----------|--------|
| `_RecordType` | Записи регистра | `AccumulationRegister_Продажи_RecordType` |

## Табличные части

Табличные части объектов доступны как отдельные ресурсы.
Имя ресурса: **`ИмяРесурсаОбъекта_ИмяТабличнойЧасти`**.

Например, табличная часть `Адреса` справочника `Сотрудники`:
`Catalog_Сотрудники_Адреса`

Стандартные поля табличной части:
- `LineNumber` — номер строки
- реквизиты табличной части
- `_Key` / `_Type` суффиксы для ссылочных и составных полей

## Стандартные поля OData по типам объектов

### Справочники

| Поле OData | Описание |
|------------|----------|
| `Ref_Key` | UUID элемента (ссылка) |
| `DeletionMark` | Пометка удаления |
| `Predefined` | Предопределённый элемент |
| `IsFolder` | Является ли папкой (иерархический справочник) |
| `Parent_Key` | UUID родителя (иерархия) |
| `Code` | Код элемента |
| `Description` | Наименование элемента |

### Документы

| Поле OData | Описание |
|------------|----------|
| `Ref_Key` | UUID документа |
| `DeletionMark` | Пометка удаления |
| `Date` | Дата документа |
| `Number` | Номер документа |
| `Posted` | Проведён |

### Регистры сведений (записи)

| Поле OData | Описание |
|------------|----------|
| `RecordKey` | Ключ записи |
| `Period` | Период (если регистр периодический) |

Для регистров сведений (срез последних) доступны: `Period`, измерения и ресурсы.

### Табличные части

| Поле OData | Описание |
|------------|----------|
| `LineNumber` | Номер строки |

## Суффиксы полей

1С добавляет специальные суффиксы к именам реквизитов в OData:

| Суффикс | Когда появляется | Описание | Пример |
|---------|-------------------|----------|--------|
| `_Key` | Реквизит ссылочного типа | UUID связанного объекта | `Организация_Key` |
| `_Type` | Реквизит составного типа (несколько типов) | Тип значения | `Владелец_Type` |

Используйте `_Key` для фильтрации по ссылке, `_Type` — для определения типа в составных полях.

## Параметры OData-запросов

| Параметр | Описание | Пример |
|----------|----------|--------|
| `$top` | Ограничить кол-во записей | `$top=10` |
| `$skip` | Пропустить N записей | `$skip=20` |
| `$filter` | Фильтрация | `$filter=DeletionMark eq false` |
| `$select` | Выбрать конкретные поля | `$select=Ref_Key,Description` |
| `$orderby` | Сортировка | `$orderby=Description asc` |
| `$expand` | Раскрыть связанные объекты | `$expand=Организация` |
| `$format` | Формат ответа | `$format=json` |

## Операторы фильтрации

### Операторы сравнения

| Оператор | Описание | Пример |
|----------|----------|--------|
| `eq` | Равно | `$filter=DeletionMark eq false` |
| `ne` | Не равно | `$filter=Code ne '000'` |
| `gt` | Больше | `$filter=Date gt datetime'2024-01-01T00:00:00'` |
| `ge` | Больше или равно | `$filter=Date ge datetime'2024-01-01T00:00:00'` |
| `lt` | Меньше | `$filter=Date lt datetime'2024-12-31T23:59:59'` |
| `le` | Меньше или равно | `$filter=Code le '000100'` |

### Строковые функции

| Функция | Описание | Пример |
|---------|-----------|--------|
| `substringof` | Содержит подстроку | `$filter=substringof('Иванов', Description) eq true` |
| `startswith` | Начинается с | `$filter=startswith(Code, '000')` |
| `endswith` | Заканчивается на | `$filter=endswith(Description, 'ов')` |

> **Важно:** 1С использует OData v3. Синтаксис `substringof` отличается от OData v4:
> - v3: `substringof('значение', Поле) eq true`
> - v4: `contains(Поле, 'значение')` — **не работает** в 1С!

### Логические операторы

| Оператор | Описание | Пример |
|----------|----------|--------|
| `and` | Логическое И | `$filter=DeletionMark eq false and Date gt datetime'2024-01-01T00:00:00'` |
| `or` | Логическое ИЛИ | `$filter=Code eq '001' or Code eq '002'` |
| `not` | Отрицание | `$filter=not(DeletionMark eq true)` |

### Формат значений в фильтрах

| Тип значения | Синтаксис | Пример |
|--------------|-----------|--------|
| Строка | Одинарные кавычки | `'Иванов'` |
| Число | Без кавычек | `100` |
| Булево | `true` / `false` | `eq true` |
| Дата | `datetime'YYYY-MM-DDTHH:MM:SS'` | `datetime'2024-01-01T00:00:00'` |
| UUID (Ref_Key) | `guid'uuid'` | `guid'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'` |

## URL-кодирование кириллических имён

Имена объектов 1С (на русском) нужно URL-кодировать перед подстановкой в URL.
Используйте функцию `encodeURIComponent()` — это стандарт URL-кодировки UTF-8.

Примеры закодированных имён:

| Имя 1С | URL-encoded |
|--------|-------------|
| `Сотрудники` | `%D0%A1%D0%BE%D1%82%D1%80%D1%83%D0%B4%D0%BD%D0%B8%D0%BA%D0%B8` |
| `ФизическиеЛица` | `%D0%A4%D0%B8%D0%B7%D0%B8%D1%87%D0%B5%D1%81%D0%BA%D0%B8%D0%B5%D0%9B%D0%B8%D1%86%D0%B0` |
| `Организации` | `%D0%9E%D1%80%D0%B3%D0%B0%D0%BD%D0%B8%D0%B7%D0%B0%D1%86%D0%B8%D0%B8` |

## Примеры запросов через fetch

### Запросить данные справочника

```
fetch(
  url="http://localhost/your_base/odata/standard.odata/Catalog_%D0%A1%D0%BE%D1%82%D1%80%D1%83%D0%B4%D0%BD%D0%B8%D0%BA%D0%B8?$top=10&$format=json",
  method="GET",
  headers={"Authorization": "Basic <base64>", "Accept": "application/json"}
)
```

### Запросить данные документа

```
fetch(
  url="http://localhost/your_base/odata/standard.odata/Document_%D0%9E%D1%82%D0%BF%D1%83%D1%81%D0%BA?$top=5&$format=json",
  method="GET",
  headers={"Authorization": "Basic <base64>", "Accept": "application/json"}
)
```

### Получить конкретную запись по GUID

```
fetch(
  url="http://localhost/your_base/odata/standard.odata/Catalog_%D0%A1%D0%BE%D1%82%D1%80%D1%83%D0%B4%D0%BD%D0%B8%D0%BA%D0%B8(guid'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')?$format=json",
  method="GET",
  headers={"Authorization": "Basic <base64>", "Accept": "application/json"}
)
```

### Подсчитать количество записей (`$count`)

```
fetch(
  url="http://localhost/your_base/odata/standard.odata/Catalog_%D0%A1%D0%BE%D1%82%D1%80%D1%83%D0%B4%D0%BD%D0%B8%D0%BA%D0%B8/$count",
  method="GET",
  headers={"Authorization": "Basic <base64>", "Accept": "application/json"}
)
```

### Выбрать конкретные поля (`$select`)

```
fetch(
  url="http://localhost/your_base/odata/standard.odata/Catalog_%D0%A1%D0%BE%D1%82%D1%80%D1%83%D0%B4%D0%BD%D0%B8%D0%BA%D0%B8?$select=Ref_Key,Description,Code&$format=json",
  method="GET",
  headers={"Authorization": "Basic <base64>", "Accept": "application/json"}
)
```

### Раскрыть связанный объект (`$expand`)

```
fetch(
  url="http://localhost/your_base/odata/standard.odata/Document_%D0%9E%D1%82%D0%BF%D1%83%D1%81%D0%BA?$expand=Организация&$top=5&$format=json",
  method="GET",
  headers={"Authorization": "Basic <base64>", "Accept": "application/json"}
)
```

### Фильтрация по дате

```
fetch(
  url="http://localhost/your_base/odata/standard.odata/Document_%D0%9E%D1%82%D0%BF%D1%83%D1%81%D0%BA?$filter=Date ge datetime'2024-01-01T00:00:00' and Date le datetime'2024-12-31T23:59:59'&$format=json",
  method="GET",
  headers={"Authorization": "Basic <base64>", "Accept": "application/json"}
)
```

### Фильтрация по наименованию (OData v3 синтаксис!)

```
fetch(
  url="http://localhost/your_base/odata/standard.odata/Catalog_%D0%A1%D0%BE%D1%82%D1%80%D1%83%D0%B4%D0%BD%D0%B8%D0%BA%D0%B8?$filter=substringof('Иванов', Description) eq true&$format=json",
  method="GET",
  headers={"Authorization": "Basic <base64>", "Accept": "application/json"}
)
```

### Записи регистра сведений

```
fetch(
  url="http://localhost/your_base/odata/standard.odata/InformationRegister_%D0%A6%D0%B5%D0%BD%D1%8B_RecordType?$top=10&$format=json",
  method="GET",
  headers={"Authorization": "Basic <base64>", "Accept": "application/json"}
)
```

### Срез последних регистра сведений

```
fetch(
  url="http://localhost/your_base/odata/standard.odata/InformationRegister_%D0%A6%D0%B5%D0%BD%D1%8B_%D0%A1%D1%80%D0%B5%D0%B7%D0%9F%D0%BE%D1%81%D0%BB%D0%B5%D0%B4%D0%BD%D0%B8%D1%85?$format=json",
  method="GET",
  headers={"Authorization": "Basic <base64>", "Accept": "application/json"}
)
```

### Запрос константы

```
fetch(
  url="http://localhost/your_base/odata/standard.odata/Constant_%D0%90%D0%B4%D1%80%D0%B5%D1%81%D0%9F%D1%83%D0%B1%D0%BB%D0%B8%D0%BA%D0%B0%D1%86%D0%B8%D0%B8/$value",
  method="GET",
  headers={"Authorization": "Basic <base64>", "Accept": "application/json"}
)
```

### Запрос перечисления

```
fetch(
  url="http://localhost/your_base/odata/standard.odata/Enum_%D0%9F%D0%BE%D0%BB%D0%A4%D0%B8%D0%B7%D0%B8%D1%87%D0%B5%D1%81%D0%BA%D0%BE%D0%B3%D0%BE%D0%9B%D0%B8%D1%86%D0%B0?$format=json",
  method="GET",
  headers={"Authorization": "Basic <base64>", "Accept": "application/json"}
)
```

### Запрос табличной части

```
fetch(
  url="http://localhost/your_base/odata/standard.odata/Catalog_%D0%A1%D0%BE%D1%82%D1%80%D1%83%D0%B4%D0%BD%D0%B8%D0%BA%D0%B8_%D0%90%D0%B4%D1%80%D0%B5%D1%81%D0%B0?$format=json",
  method="GET",
  headers={"Authorization": "Basic <base64>", "Accept": "application/json"}
)
```

## Формат ответа

```json
{
  "odata.metadata": "...",
  "value": [
    {
      "Ref_Key": "uuid",
      "DataVersion": "...",
      "DeletionMark": false,
      "Code": "000000001",
      "Description": "Название элемента",
      "Предопределенный": false
    }
  ]
}
```

## Включить объект в OData

Если объект не появляется в списке сущностей, он не опубликован.
Выполните в 1С (режим Сервер / Внешнее соединение / Интеграция):

```bsl
МассивОбъектов = Новый Массив();
МассивОбъектов.Добавить(Метаданные.Справочники.ИмяСправочника);
УстановитьСоставСтандартногоИнтерфейсаOData(МассивОбъектов);
```

## Типичные ошибки

### 401 Unauthorized / 401.5

Неверная кодировка credentials. Убедитесь, что используете заголовок `Authorization: Basic`
с корректной Base64-кодировкой строки `логин:пароль` в UTF-8.

### Объект не найден в списке сущностей

Объект не опубликован через OData. Выполните `enable-odata.bsl` или используйте обработку `EnableODataInterface.epf`.

### `contains` не работает

1С использует OData v3, где вместо `contains(Поле, 'значение')` нужно использовать
`substringof('значение', Поле) eq true`.