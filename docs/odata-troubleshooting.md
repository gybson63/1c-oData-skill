# OData: типичные проблемы

> Перенесено из `problems.md` — заметки по отладке OData URL.

---

## Кодирование `$filter` в URL

### Правильно

Пробелы в фильтре кодируются как `%20`:

```text
http://localhost/zup_gazaliev/odata/standard.odata/Catalog_%D0%9E%D1%80%D0%B3%D0%B0%D0%BD%D0%B8%D0%B7%D0%B0%D1%86%D0%B8%D0%B8/$count?$filter=DeletionMark%20eq%20false
```

### Неправильно

Пробелы заменены на `+` (частая ошибка при формировании query string):

```text
http://localhost/zup_gazaliev/odata/standard.odata/Catalog_%D0%9E%D1%80%D0%B3%D0%B0%D0%BD%D0%B8%D0%B7%D0%B0%D1%86%D0%B8%D0%B8/$count?$filter=DeletionMark+eq+false
```

1С OData может отклонить такой URL или вернуть неожиданный результат.

### Причина

При сборке URL некоторые HTTP-клиенты используют `application/x-www-form-urlencoded` семантику (`+` = пробел) вместо RFC 3986 (`%20`).

Проверьте `_request_raw` и места, где query string собирается вручную или через `urllib.parse.urlencode` без `quote_via=urllib.parse.quote`.

---

## Связанные документы

- [`full-guide.md`](full-guide.md) §«Типичные ошибки»
- [`skills/odata/SKILL.md`](../skills/odata/SKILL.md)
