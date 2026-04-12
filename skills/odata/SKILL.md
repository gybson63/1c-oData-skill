---
name: odata
description: Получение данных из 1С:Предприятие через стандартный OData-интерфейс
---

# OData — запросы к данным 1С

## Описание

Скилл для получения данных из 1С:Предприятие через стандартный REST OData-интерфейс.
Работает с любой конфигурацией 1С, где опубликована база через веб-сервер.

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

## Подготовка переменных

Перед выполнением запросов загрузите переменные из `env.json` через Node.js:

```bash
ODATA_URL=$(node -e "const d=require('./env.json').default; process.stdout.write(d.odata_url)")

ODATA_AUTH=$(node -e "const d=require('./env.json').default; process.stdout.write(Buffer.from(d.odata_user+':'+d.odata_password).toString('base64'))")
```

> **Важно:** `curl -u "user:pass"` не работает с кириллицей на Windows — используй только заголовок `Authorization: Basic`.
> PowerShell не подходит для base64-кодирования кириллических паролей — ломает кодировку.

## Проверить доступные сущности

```bash
curl -s \
  -H "Authorization: Basic $ODATA_AUTH" \
  -H "Accept: application/json" \
  "$ODATA_URL/"
```

## Запросить данные справочника

```bash
# URL-закодируйте имя справочника:
# python -c "from urllib.parse import quote; print(quote('ИмяСправочника'))"

curl -s \
  -H "Authorization: Basic $ODATA_AUTH" \
  -H "Accept: application/json" \
  "$ODATA_URL/Catalog_%D0%A1%D0%BE%D1%82%D1%80%D1%83%D0%B4%D0%BD%D0%B8%D0%BA%D0%B8?\$top=10&\$format=json"
```

## Запросить данные документа

```bash
curl -s \
  -H "Authorization: Basic $ODATA_AUTH" \
  -H "Accept: application/json" \
  "$ODATA_URL/Document_%D0%9E%D1%82%D0%BF%D1%83%D1%81%D0%BA?\$top=5&\$format=json"
```

## Параметры OData-запросов

| Параметр | Описание | Пример |
|----------|----------|--------|
| `$top` | Ограничить кол-во записей | `\$top=10` |
| `$skip` | Пропустить N записей | `\$skip=20` |
| `$filter` | Фильтрация | `\$filter=DeletionMark eq false` |
| `$select` | Выбрать конкретные поля | `\$select=Ref_Key,Description` |
| `$orderby` | Сортировка | `\$orderby=Description asc` |
| `$format` | Формат ответа | `\$format=json` |

## Операторы фильтрации

| Оператор | Описание | Пример |
|----------|----------|--------|
| `eq` | Равно | `\$filter=DeletionMark eq false` |
| `ne` | Не равно | `\$filter=Code ne '000'` |
| `contains` | Содержит подстроку | `\$filter=contains(Description,'Иванов')` |
| `gt` / `lt` | Больше / меньше | `\$filter=Date gt datetime'2024-01-01T00:00:00'` |

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

## URL-кодирование кириллических имён

```bash
python -c "from urllib.parse import quote; print(quote('ИмяОбъекта'))"
```

Часто используемые типы объектов в URL:

| Тип 1С | Префикс в OData |
|--------|-----------------|
| Справочник | `Catalog_` |
| Документ | `Document_` |
| РегистрСведений | `InformationRegister_` |
| РегистрНакопления | `AccumulationRegister_` |
| РегистрРасчета | `CalculationRegister_` |
| ПланВидовРасчета | `ChartOfCalculationTypes_` |

## Включить объект в OData

Если объект не появляется в списке сущностей, он не опубликован.
Выполните в 1С (режим Сервер / Внешнее соединение / Интеграция):

```bsl
МассивОбъектов = Новый Массив();
МассивОбъектов.Добавить(Метаданные.Справочники.ИмяСправочника);
УстановитьСоставСтандартногоИнтерфейсаOData(МассивОбъектов);
```
