---
name: 1cconfinfo
description: Анализ структуры конфигурации 1С:Предприятие — по XML-файлам выгрузки или через OData $metadata. Если Configuration.xml недоступен, автоматически использует OData.
---

# 1cconfinfo — анализ конфигурации 1С

## Описание

Скилл для анализа структуры конфигурации 1С:Предприятие.  
Поддерживает два источника данных:

| Источник | Когда использовать |
|----------|-------------------|
| **XML-выгрузка** (`Configuration.xml`) | Есть выгрузка конфигурации в файлы |
| **OData `$metadata`** | Нет выгрузки, но есть опубликованная база |

**Приоритет**: сначала ищи `Configuration.xml`. Если файла нет — используй OData.

---

## Источник 1: XML-выгрузка конфигурации

Используй скилл `cf-info` для анализа XML-выгрузки:

```powershell
python .claude/skills/cf-info/scripts/cf-info.py -ConfigPath <путь_к_Configuration.xml_или_папке>
```

Или через PowerShell:

```powershell
powershell -NoProfile -File .claude/skills/cf-info/scripts/cf-info.ps1 -ConfigPath <путь>
```

Режимы: `overview` (default), `brief`, `full`.

### Структура выгрузки

`Configuration.xml` — корневой дескриптор метаданных:
- имя, версию, поставщика
- ссылки на все объекты конфигурации

| Тип объекта | Папка |
|-------------|-------|
| Справочники | `Catalogs/` |
| Документы | `Documents/` |
| РегистрыСведений | `InformationRegisters/` |
| РегистрыНакопления | `AccumulationRegisters/` |
| Перечисления | `Enums/` |
| Отчёты | `Reports/` |
| Обработки | `DataProcessors/` |
| ОбщиеМодули | `CommonModules/` |
| Роли | `Roles/` |
| Подсистемы | `Subsystems/` |
| РегламентныеЗадания | `ScheduledJobs/` |
| ПланыОбмена | `ExchangePlans/` |
| HTTPСервисы | `HTTPServices/` |

---

## Источник 2: OData $metadata (когда нет Configuration.xml)

Если выгрузки нет, используй скрипт `odata-cfg-info.py`:

```bash
python skills/1cconfinfo/scripts/odata-cfg-info.py [-Mode overview|brief|full]
```

Скрипт читает credentials из `env.json`, запрашивает `{odata_url}/$metadata`, **кэширует** ответ в `.cache/` и показывает состав опубликованных объектов.

### Параметры скрипта

| Параметр | Описание | По умолчанию |
|----------|----------|--------------|
| `-Mode` | overview / brief / full | overview |
| `-EnvFile` | Путь к env.json | `env.json` |
| `-EnvProfile` | Профиль в env.json | `default` |
| `-ODataUrl` | Переопределить URL | из env.json |
| `-CacheDir` | Папка для кэша | `.cache` |
| `-CacheTTL` | Время жизни кэша (сек) | 3600 |
| `-ForceRefresh` | Игнорировать кэш | false |

### Примеры

```bash
# Обзор (из кэша если есть, иначе — из OData)
python skills/1cconfinfo/scripts/odata-cfg-info.py

# Полный список объектов
python skills/1cconfinfo/scripts/odata-cfg-info.py -Mode full

# Принудительно обновить кэш
python skills/1cconfinfo/scripts/odata-cfg-info.py -ForceRefresh

# Краткая строка
python skills/1cconfinfo/scripts/odata-cfg-info.py -Mode brief

# Другой профиль
python skills/1cconfinfo/scripts/odata-cfg-info.py -EnvProfile prod
```

### Как работает кэш

- Файл кэша: `.cache/odata_metadata_<hash>.xml` (hash от URL базы)
- TTL по умолчанию: **1 час** (3600 сек)
- Повторные вызовы в течение часа работают без сетевых запросов
- `-ForceRefresh` принудительно обновляет кэш

### Ограничения OData vs XML

| Возможность | XML-выгрузка | OData |
|-------------|:---:|:---:|
| Все объекты конфигурации | ✓ | ✗ (только опубликованные) |
| Реквизиты объектов | ✓ | ✓ (через EntityType) |
| Версия конфигурации | ✓ | ✗ |
| Роли, подсистемы, модули | ✓ | ✗ |
| Работает без выгрузки | ✗ | ✓ |

---

## Настройка env.json

```json
{
  "default": {
    "odata_url": "http://localhost/your_base/odata/standard.odata",
    "odata_user": "Администратор",
    "odata_password": "пароль"
  }
}
```

> **Важно:** Для кириллических паролей используй только этот скрипт.  
> `curl -u` и PowerShell ломают кодировку при base64.

---

## Алгоритм выбора источника

```
1. Есть ли Configuration.xml (или папка с ним)?
   ДА → использовать cf-info (полная информация)
   НЕТ → перейти к п.2

2. Есть ли env.json с odata_url?
   ДА → использовать odata-cfg-info.py (опубликованные объекты)
   НЕТ → попросить пользователя указать путь к выгрузке или настроить env.json
```

---

## Структура файла объекта (XML-выгрузка)

```xml
<MetaDataObject xmlns="..." version="2.20">
  <Catalog uuid="...">
    <Properties>
      <Name>ИмяОбъекта</Name>
      <Synonym><v8:item><v8:content>Синоним</v8:content></v8:item></Synonym>
    </Properties>
    <ChildObjects>
      <Attribute uuid="...">
        <Properties>
          <Name>ИмяРеквизита</Name>
          <Type><v8:Type>cfg:CatalogRef.ДругойСправочник</v8:Type></Type>
        </Properties>
      </Attribute>
    </ChildObjects>
  </Catalog>
</MetaDataObject>
```

## Типы ссылок в реквизитах

```xml
<v8:Type>cfg:CatalogRef.ИмяСправочника</v8:Type>
<v8:Type>cfg:DocumentRef.ИмяДокумента</v8:Type>
<v8:Type>cfg:EnumRef.ИмяПеречисления</v8:Type>
<v8:Type>xsd:string</v8:Type>
<v8:Type>xsd:decimal</v8:Type>
<v8:Type>xsd:boolean</v8:Type>
<v8:Type>xsd:dateTime</v8:Type>
```

## Соглашения об именовании

| Паттерн | Пример | Значение |
|---------|--------|----------|
| `ПрисоединенныеФайлы` суффикс | `СотрудникиПрисоединенныеФайлы` | Каталог прикреплённых файлов |
| `бит_` префикс | `бит_Доверенность` | Кастомный объект доработки |
| `Удалить` префикс | `УдалитьБанки` | Устаревший объект |
| `адаптер_` префикс | `адаптер_ВходящиеСообщения` | Объект интеграционного адаптера |
