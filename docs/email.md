# Email-интерфейс бота

Запросы к данным 1С через обычную почту: пользователь пишет письмо — бот отвечает HTML-текстом и при необходимости прикладывает таблицу или график.

> Для пользователей (как формулировать вопросы): [`user-guide.md`](user-guide.md).
> Для разработчиков (тесты, GreenMail): [`email-testing.md`](email-testing.md).

---

## Возможности

- Те же агенты, что и в Telegram: OData, аналитик, analytics с графиками
- Ответ в теле письма (HTML) + опциональное вложение (HTML/CSV, PNG для графиков)
- Диалог в **цепочке писем** (thread): бот помнит контекст треда
- Префикс **`[analyze]`** в теме или теле — режим аналитика метаданных без OData
- Входящие **CSV-вложения** для сверки списков (сценарий inbound CSV)

---

## Включение

### 1. Конфигурация `env.json`

Секция `email` в профиле (`enabled: true`). Минимально нужны IMAP и SMTP:

```json
{
  "profiles": {
    "default": {
      "email": {
        "enabled": true,
        "imap_host": "imap.example.com",
        "imap_port": 993,
        "imap_user": "bot@example.com",
        "imap_password": "secret",
        "imap_folder": "INBOX",
        "imap_use_ssl": true,
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_user": "bot@example.com",
        "smtp_password": "secret",
        "smtp_use_tls": true,
        "from_address": "bot@example.com",
        "from_name": "1С OData Bot",
        "poll_interval": 30,
        "allowed_senders": ["user@company.ru"]
      }
    }
  }
}
```

| Поле | Описание |
|------|----------|
| `allowed_senders` | Белый список отправителей; пустой массив — принимать от всех |
| `poll_interval` | Интервал опроса IMAP, секунды |
| `max_fetch_records` | Лимит строк при больших выгрузках |
| `attachment_format` | `html` — полный отчёт во вложении при длинном ответе |

Полная схема: [`configuration.md`](configuration.md).

### 2. Запуск

Только email:

```bash
python -m bot --transport email --env-file env.json --profile default
```

Telegram и email одновременно:

```bash
python -m bot --transport both --env-file env.json --profile default
```

По умолчанию (`--transport telegram`) email **не** опрашивается, даже если `email.enabled: true`.

---

## Как пользоваться

1. Отправьте письмо на ящик бота (`from_address` / IMAP-ящик).
2. В **теме или теле** опишите вопрос на русском — как в Telegram.
3. Для уточнений **ответьте в том же треде** (Reply) — контекст сохраняется.
4. Для анализа метаданных без данных из базы добавьте **`[analyze]`** в начало темы или текста.

Примеры:

| Задача | Пример темы письма |
|--------|-------------------|
| Список сотрудников | `Список сотрудников с должностью и подразделением` |
| Аналитик | `[analyze] остатки отпусков — какой регистр в ЗУП?` |
| Уточнение в треде | `Re: Список сотрудников` → `Только отдел продаж` |

---

## Отличия от Telegram

| | Telegram | Email |
|---|----------|-------|
| Формат ответа | HTML в чате | HTML в теле + вложение при длинных данных |
| Графики | PNG в чате | PNG во вложении |
| Команды `/start`, `/tokens` | Да | Нет — только текст письма |
| Аналитик | `/analyze` | `[analyze]` |
| Пагинация | Inline-кнопки | Полная выгрузка до `max_fetch_records` |

---

## Безопасность

- Ограничьте `allowed_senders` корпоратными адресами.
- Пароли IMAP/SMTP храните только в `env.json` (файл не в git).
- Для тестового стенда см. [`email-testing.md`](email-testing.md) (GreenMail).

---

## Связанные документы

- [`getting-started.md`](getting-started.md) — общий запуск проекта
- [`configuration.md`](configuration.md) — все поля `email` в env.json
- [`architecture.md`](architecture.md) — ChatManager, thread context
- [`bot/README.md`](../bot/README.md) — архитектура агентов
