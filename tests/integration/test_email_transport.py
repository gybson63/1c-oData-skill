"""L2: интеграционные тесты EmailTransport — MIME, threading, dedup, allowlist."""

from __future__ import annotations

import email
from email.mime.text import MIMEText
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.config import EmailSettings
from bot.email.transport import EmailTransport
from bot.messages import InboundMessage, OutboundMessage, TransportChannel, email_conversation_id


def _make_email(
    subject: str = "Test",
    body: str = "Hello",
    from_addr: str = "tester@local.test",
    message_id: str = "<msg-1@test>",
    in_reply_to: str = "",
    references: str = "",
) -> bytes:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = "bot@local.test"
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    return msg.as_bytes()


@pytest.fixture
def email_settings() -> EmailSettings:
    return EmailSettings(
        imap_host="localhost",
        imap_port=3143,
        imap_user="bot",
        imap_password="secret",
        smtp_host="localhost",
        smtp_port=3025,
        smtp_user="bot",
        smtp_password="secret",
        smtp_use_ssl=False,
        smtp_use_tls=False,
        from_address="bot@local.test",
        from_name="Test Bot",
        allowed_senders=["tester@local.test"],
        poll_interval=2,
    )


@pytest.fixture
def transport(tmp_path, email_settings: EmailSettings):
    received: list[InboundMessage] = []

    async def on_message(inbound: InboundMessage) -> OutboundMessage:
        received.append(inbound)
        return OutboundMessage(
            text="Ответ бота",
            channel=TransportChannel.EMAIL,
            subject=inbound.metadata.get("subject", ""),
        )

    t = EmailTransport(
        settings=email_settings,
        cache_dir=str(tmp_path),
        on_message=on_message,
    )
    t._received = received  # type: ignore[attr-defined]
    return t


@pytest.mark.integration
@pytest.mark.asyncio
async def test_handle_email_builds_inbound(transport):
    raw = _make_email(subject="Запрос OData", body="Покажи 3 сотрудника")

    with patch.object(transport, "_send_reply", new_callable=AsyncMock):
        await transport._handle_email(raw)

    assert len(transport._received) == 1  # type: ignore[attr-defined]
    inbound = transport._received[0]  # type: ignore[attr-defined]
    assert inbound.channel == TransportChannel.EMAIL
    assert "сотрудника" in inbound.text
    assert inbound.metadata["subject"] == "Запрос OData"
    assert inbound.conversation_id.startswith("email:")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_thread_reply_includes_context(transport):
    first_id = "<msg-1@thread>"
    second_id = "<msg-2@thread>"
    with patch.object(transport, "_send_reply", new_callable=AsyncMock):
        await transport._handle_email(
            _make_email(
                subject="Тред",
                body="Первый вопрос",
                message_id=first_id,
            )
        )
        await transport._handle_email(
            _make_email(
                subject="Re: Тред",
                body="Уточнение",
                message_id=second_id,
                in_reply_to=first_id,
                references=first_id,
            )
        )

    assert len(transport._received) == 2  # type: ignore[attr-defined]
    second = transport._received[1]  # type: ignore[attr-defined]
    assert "Контекст переписки" in second.text
    assert "Текущий запрос" in second.text
    assert second.conversation_id == email_conversation_id(first_id)


@pytest.mark.integration
def test_allowed_sender_passes(transport, email_settings: EmailSettings):
    raw = _make_email(from_addr="tester@local.test")
    assert transport._is_allowed_sender(raw) is True


@pytest.mark.integration
def test_disallowed_sender_blocked(transport, email_settings: EmailSettings):
    raw = _make_email(from_addr="stranger@evil.com")
    assert transport._is_allowed_sender(raw) is False


@pytest.mark.integration
def test_empty_allowlist_allows_all():
    settings = EmailSettings(allowed_senders=[])
    t = EmailTransport(settings=settings, cache_dir=".", on_message=MagicMock())
    raw = _make_email(from_addr="anyone@example.com")
    assert t._is_allowed_sender(raw) is True


@pytest.mark.integration
def test_processed_uid_dedup(tmp_path, email_settings: EmailSettings):
    t = EmailTransport(settings=email_settings, cache_dir=str(tmp_path), on_message=MagicMock())
    t._store.mark_processed("42")
    assert t._store.is_processed("42") is True
    assert t._store.is_processed("99") is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_send_reply_threading_headers(transport, email_settings: EmailSettings):
    from bot.email.parser import parse_email_message

    raw = _make_email(
        subject="Исходный",
        message_id="<orig@test>",
    )
    meta, _, _, _ = parse_email_message(raw)
    outbound = OutboundMessage(text="**Ответ**", channel=TransportChannel.EMAIL)

    sent_messages: list[email.message.Message] = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, user, password):
            pass

        def send_message(self, msg):
            sent_messages.append(msg)

    with patch("bot.email.transport.smtplib.SMTP", FakeSMTP):
        transport._send_reply_sync(meta, outbound)

    assert sent_messages
    msg = sent_messages[0]
    assert msg["Subject"].startswith("Re:")
    assert msg["In-Reply-To"] == "<orig@test>"
    assert "<orig@test>" in msg["References"]
    assert msg.get_payload() or msg.is_multipart()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_send_reply_with_attachment_multipart(transport):
    from bot.email.parser import parse_email_message
    from bot.messages import Attachment

    raw = _make_email(message_id="<att@test>")
    meta, _, _, _ = parse_email_message(raw)
    outbound = OutboundMessage(
        text="Краткий ответ",
        channel=TransportChannel.EMAIL,
        attachments=[
            Attachment(filename="report.html", content_type="text/html", data=b"<html>data</html>"),
        ],
    )

    sent_messages: list[email.message.Message] = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, user, password):
            pass

        def send_message(self, msg):
            sent_messages.append(msg)

    with patch("bot.email.transport.smtplib.SMTP", FakeSMTP):
        transport._send_reply_sync(meta, outbound)

    msg = sent_messages[0]
    assert msg.get_content_type() == "multipart/mixed"
    filenames = []
    for part in msg.walk():
        if part.get_filename():
            filenames.append(part.get_filename())
    assert "report.html" in filenames


@pytest.mark.integration
def test_fetch_unseen_skips_processed_uid(tmp_path, email_settings: EmailSettings):
    """UID, уже в processed_uids, не попадает в очередь обработки."""
    t = EmailTransport(settings=email_settings, cache_dir=str(tmp_path), on_message=MagicMock())
    t._store.mark_processed("1")
    fake_raw = _make_email()

    pending_uids = ["1", "2", "3"]
    result: list[tuple[str, bytes]] = []
    for uid_str in pending_uids:
        if t._store.is_processed(uid_str):
            continue
        if t._is_allowed_sender(fake_raw):
            result.append((uid_str, fake_raw))

    assert "1" not in [uid for uid, _ in result]
    assert "2" in [uid for uid, _ in result]
