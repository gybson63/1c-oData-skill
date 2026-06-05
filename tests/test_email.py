"""Тесты email-модуля: парсер, вложения, контекст цепочки."""

from __future__ import annotations

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from bot.email.attachments import extract_text_from_attachment, format_attachments_for_context
from bot.email.formatter import build_reply_subject, format_email_reply, markdownish_to_html
from bot.email.parser import (
    html_to_text,
    parse_email_message,
    strip_quoted_reply,
)
from bot.email.store import EmailThreadStore
from bot.email.thread_context import ThreadContextConfig, build_thread_context, build_user_text_from_thread
from bot.messages import Attachment, EmailMessageMeta, conversation_id_to_chat_id, email_conversation_id


def _make_email(
    subject: str = "Test",
    body: str = "Hello",
    html: str | None = None,
    from_addr: str = "user@example.com",
    message_id: str = "<msg-1@test>",
    in_reply_to: str = "",
    references: str = "",
) -> bytes:
    if html:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
    else:
        msg = MIMEText(body, "plain", "utf-8")

    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = "bot@example.com"
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    return msg.as_bytes()


class TestEmailParser:
    def test_parse_simple_email(self):
        raw = _make_email(subject="Запрос", body="Покажи сотрудников")
        meta, plain, html, attachments = parse_email_message(raw)

        assert meta.subject == "Запрос"
        assert plain == "Покажи сотрудников"
        assert meta.sender == "user@example.com"
        assert not attachments

    def test_parse_html_email(self):
        raw = _make_email(
            body="",
            html="<p>Покажи <strong>сотрудников</strong></p>",
        )
        meta, plain, html, _ = parse_email_message(raw)
        assert "сотрудников" in plain
        assert "**сотрудников**" in plain or "сотрудников" in plain

    def test_thread_id_from_references(self):
        raw = _make_email(
            message_id="<msg-3@test>",
            in_reply_to="<msg-2@test>",
            references="<msg-1@test> <msg-2@test>",
        )
        meta, _, _, _ = parse_email_message(raw)
        assert meta.thread_id == "<msg-1@test>"

    def test_strip_quoted_reply(self):
        text = "Новый вопрос\n\n> старый текст\n> ещё текст"
        assert strip_quoted_reply(text) == "Новый вопрос"

    def test_strip_on_wrote_pattern(self):
        text = "Новый запрос\n\nOn Mon, Jan 1 2025 John wrote:\nOld text"
        result = strip_quoted_reply(text)
        assert "Новый запрос" in result
        assert "Old text" not in result

    def test_html_to_text_table(self):
        html = "<table><tr><th>Name</th><th>Age</th></tr><tr><td>Ivan</td><td>30</td></tr></table>"
        text = html_to_text(html)
        assert "Ivan" in text
        assert "30" in text


class TestAttachments:
    def test_extract_text_file(self):
        att = Attachment(filename="data.txt", content_type="text/plain", data=b"Hello world")
        result = extract_text_from_attachment(att)
        assert "Hello world" in result

    def test_extract_csv(self):
        csv_data = "Name,Age\nIvan,30\nPetr,25\n"
        att = Attachment(filename="data.csv", content_type="text/csv", data=csv_data.encode())
        result = extract_text_from_attachment(att)
        assert "Ivan" in result
        assert "30" in result

    def test_format_attachments_for_context(self):
        atts = [
            Attachment(filename="note.txt", content_type="text/plain", data=b"Important note"),
        ]
        result = format_attachments_for_context(atts)
        assert "Important note" in result
        assert "note.txt" in result


class TestThreadContext:
    def _make_thread_messages(self, count: int):
        from bot.email.store import ThreadMessage

        return [
            ThreadMessage(
                meta=EmailMessageMeta(
                    message_id=f"<msg-{i}>",
                    subject=f"Re: Test {i}",
                    sender=f"user{i}@example.com",
                    date=f"2025-01-{i + 1:02d}T10:00:00",
                    thread_id="<thread-1>",
                ),
                body=f"Сообщение номер {i}. " + ("x" * 500),
            )
            for i in range(count)
        ]

    def test_short_thread_full_context(self):
        thread = self._make_thread_messages(2)
        ctx = build_thread_context(thread)
        assert "Сообщение номер 0" in ctx
        assert "Сообщение номер 1" in ctx

    def test_long_thread_compression(self):
        thread = self._make_thread_messages(10)
        config = ThreadContextConfig(
            max_total_chars=3000,
            max_message_chars=500,
            keep_full_recent=2,
            keep_first=True,
            middle_summary_chars=100,
        )
        ctx = build_thread_context(thread, config)
        assert "сжато" in ctx.lower() or "обрезан" in ctx.lower() or len(ctx) <= 3500
        assert "Сообщение номер 0" in ctx  # первое письмо
        assert "Сообщение номер 9" in ctx  # последнее

    def test_build_user_text(self):
        thread = self._make_thread_messages(3)
        latest, full = build_user_text_from_thread(thread)
        assert "Сообщение номер 2" in latest
        assert "Сообщение номер 0" in full

    def test_thread_with_bot_replies(self, tmp_path):
        from bot.email.store import EmailThreadStore

        store = EmailThreadStore(tmp_path)
        meta = EmailMessageMeta(
            message_id="<u1>",
            subject="Отпуск",
            sender="user@test",
            thread_id="<thread-1>",
        )
        store.add_message(meta, "Сколько в отпуске?")
        store.add_bot_reply(
            thread_id="<thread-1>",
            message_id="<b1>",
            body="1",
            subject="Re: Отпуск",
            in_reply_to="<u1>",
        )
        store.add_message(
            EmailMessageMeta(
                message_id="<u2>",
                subject="Re: Отпуск",
                sender="user@test",
                thread_id="<thread-1>",
            ),
            "Какой?",
        )
        ctx = build_thread_context(store.get_thread("<thread-1>"))
        assert "Сколько в отпуске" in ctx
        assert "Ответ бота" in ctx
        assert "1" in ctx
        assert "Какой?" in ctx


class TestEmailFormatter:
    def test_markdownish_to_html_bold(self):
        result = markdownish_to_html("**Важно**: текст")
        assert "<strong>" in result

    def test_markdownish_table(self):
        text = "| Name | Age |\n| --- | --- |\n| Ivan | 30 |"
        result = markdownish_to_html(text)
        assert "<table>" in result
        assert "Ivan" in result

    def test_format_email_reply(self):
        html, plain = format_email_reply("<p>Ответ</p>", token_footer="100 tokens")
        assert "Ответ" in html
        assert "100 tokens" in plain

    def test_build_reply_subject(self):
        assert build_reply_subject("Запрос") == "Re: Запрос"
        assert build_reply_subject("Re: Запрос") == "Re: Запрос"


class TestEmailStore:
    def test_thread_persistence(self, tmp_path):
        store = EmailThreadStore(tmp_path)
        meta = EmailMessageMeta(
            message_id="<msg-1>",
            subject="Test",
            sender="user@example.com",
            thread_id="<thread-1>",
        )
        store.add_message(meta, "Hello")
        thread = store.get_thread("<thread-1>")
        assert len(thread) == 1
        assert thread[0].body == "Hello"

        # Дедупликация
        store.add_message(meta, "Hello again")
        thread = store.get_thread("<thread-1>")
        assert len(thread) == 1

    def test_processed_uids(self, tmp_path):
        store = EmailThreadStore(tmp_path)
        assert not store.is_processed("123")
        store.mark_processed("123")
        assert store.is_processed("123")


class TestConversationId:
    def test_telegram_id(self):
        assert conversation_id_to_chat_id("tg:12345") == 12345

    def test_email_id_negative(self):
        cid = email_conversation_id("<thread-abc>")
        chat_id = conversation_id_to_chat_id(cid)
        assert chat_id < 0

    def test_email_id_stable(self):
        cid = email_conversation_id("<thread-abc>")
        assert conversation_id_to_chat_id(cid) == conversation_id_to_chat_id(cid)
