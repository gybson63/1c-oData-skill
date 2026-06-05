#!/usr/bin/env python3
"""Email transport: IMAP polling + SMTP replies."""

from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import smtplib
import ssl
from collections.abc import Awaitable, Callable
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid

from bot.config import EmailSettings
from bot.email.attachments import format_attachments_for_context
from bot.email.formatter import build_reply_subject, format_email_reply
from bot.email.parser import parse_email_message
from bot.email.store import EmailThreadStore
from bot.email.thread_context import ThreadContextConfig, build_user_text_from_thread
from bot.messages import (
    InboundMessage,
    OutboundMessage,
    TransportChannel,
    email_conversation_id,
)
from bot.metrics import session_tokens

log = logging.getLogger(__name__)

MessageHandler = Callable[[InboundMessage], Awaitable[OutboundMessage | None]]


class EmailTransport:
    """IMAP/SMTP транспорт для обработки email-запросов."""

    def __init__(
        self,
        settings: EmailSettings,
        cache_dir: str,
        on_message: MessageHandler,
    ) -> None:
        self._settings = settings
        self._on_message = on_message
        self._store = EmailThreadStore(f"{cache_dir}/email_threads")
        self._thread_config = ThreadContextConfig(
            max_total_chars=settings.context_max_chars,
            max_message_chars=settings.context_message_max_chars,
            keep_full_recent=settings.context_keep_recent,
            keep_first=settings.context_keep_first,
            middle_summary_chars=settings.context_middle_summary_chars,
        )
        self._running = False

    async def run_forever(self) -> None:
        """Основной цикл опроса IMAP."""
        self._running = True
        log.info(
            "Email transport запущен: IMAP %s:%d, poll=%ds",
            self._settings.imap_host,
            self._settings.imap_port,
            self._settings.poll_interval,
        )
        log.info("Бот приступил к работе")

        while self._running:
            try:
                await self._poll_once()
            except Exception:
                log.exception("Ошибка в цикле опроса email")
            await asyncio.sleep(self._settings.poll_interval)

    def stop(self) -> None:
        self._running = False

    async def _poll_once(self) -> None:
        """Один цикл опроса — IMAP в thread pool, обработка async."""
        loop = asyncio.get_event_loop()
        pending = await loop.run_in_executor(None, self._fetch_unseen)
        for uid_str, raw_bytes in pending:
            try:
                await self._handle_email(raw_bytes)
                self._store.mark_processed(uid_str)
            except Exception:
                log.exception("Ошибка обработки письма UID=%s", uid_str)

    def _fetch_unseen(self) -> list[tuple[str, bytes]]:
        """Синхронный опрос IMAP — вернуть список (uid, raw_bytes)."""
        settings = self._settings
        result: list[tuple[str, bytes]] = []

        try:
            imap: imaplib.IMAP4 | imaplib.IMAP4_SSL
            if settings.imap_use_ssl:
                imap = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port)
            else:
                imap = imaplib.IMAP4(settings.imap_host, settings.imap_port)

            imap.login(settings.imap_user, settings.imap_password)
            imap.select(settings.imap_folder)

            status, messages = imap.search(None, "UNSEEN")
            if status != "OK":
                log.warning("IMAP SEARCH failed: %s", status)
                imap.logout()
                return result

            uids = messages[0].split()
            log.debug("Найдено %d непрочитанных писем", len(uids))

            for uid in uids:
                uid_str = uid.decode() if isinstance(uid, bytes) else str(uid)
                if self._store.is_processed(uid_str):
                    continue

                status, data = imap.fetch(uid, "(RFC822)")
                if status != "OK" or not data or not data[0]:
                    continue

                raw_bytes = data[0][1]
                if not isinstance(raw_bytes, bytes):
                    continue

                if not self._is_allowed_sender(raw_bytes):
                    log.info("Письмо UID=%s от неразрешённого отправителя, пропуск", uid_str)
                    self._store.mark_processed(uid_str)
                    continue

                result.append((uid_str, raw_bytes))

            imap.logout()
        except imaplib.IMAP4.error as e:
            log.error("IMAP error: %s", e)

        return result

    def _is_allowed_sender(self, raw_bytes: bytes) -> bool:
        """Проверить, разрешён ли отправитель."""
        allowed = self._settings.allowed_senders
        if not allowed:
            return True

        msg = email.message_from_bytes(raw_bytes)
        from_header = msg.get("From", "")
        from_lower = from_header.lower()
        return any(a.lower() in from_lower for a in allowed)

    async def _handle_email(self, raw_bytes: bytes) -> None:
        """Async обработка письма."""
        meta, plain, html_body, attachments = parse_email_message(raw_bytes)
        attachment_text = format_attachments_for_context(attachments)

        thread = self._store.add_message(
            meta,
            plain,
            html_body,
            attachments,
            attachment_text,
        )

        latest_text, thread_context = build_user_text_from_thread(thread, self._thread_config)

        # Собрать user_text: контекст цепочки + последнее сообщение
        if len(thread) > 1 and thread_context:
            user_text = (
                f"Контекст переписки ({len(thread)} писем):\n\n"
                f"{thread_context}\n\n"
                f"--- Текущий запрос ---\n{latest_text}"
            )
        else:
            user_text = latest_text

        inbound = InboundMessage(
            conversation_id=email_conversation_id(meta.thread_id),
            channel=TransportChannel.EMAIL,
            text=user_text,
            sender=meta.sender,
            attachments=attachments,
            metadata={
                "message_id": meta.message_id,
                "subject": meta.subject,
                "thread_id": meta.thread_id,
            },
            thread_context=thread_context,
        )

        try:
            outbound = await self._on_message(inbound)
        except Exception:
            log.exception("Ошибка обработки email от %s", meta.sender)
            return

        if outbound:
            await self._send_reply(meta, outbound)

    async def _send_reply(self, original_meta, outbound: OutboundMessage) -> None:
        """Отправить ответ по SMTP."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._send_reply_sync, original_meta, outbound)

    def _send_reply_sync(self, original_meta, outbound: OutboundMessage) -> None:
        settings = self._settings

        token_footer = ""
        chat_id = InboundMessage(
            conversation_id=email_conversation_id(original_meta.thread_id),
            channel=TransportChannel.EMAIL,
            text="",
        ).chat_id
        st = session_tokens.get(chat_id)
        if st.requests > 0:
            token_footer = st.format_compact()

        html_body, plain_body = format_email_reply(
            outbound.text,
            token_footer=token_footer if not outbound.attachments else "",
            original_subject=original_meta.subject,
        )

        subject = build_reply_subject(original_meta.subject or outbound.subject)

        if outbound.attachments:
            msg = MIMEMultipart("mixed")
        else:
            msg = MIMEMultipart("alternative")

        msg["Subject"] = subject
        msg["From"] = formataddr((settings.from_name, settings.from_address or settings.smtp_user))
        msg["To"] = original_meta.sender
        msg["Message-ID"] = make_msgid(domain=settings.message_id_domain)

        if original_meta.message_id:
            msg["In-Reply-To"] = original_meta.message_id
            refs = original_meta.references + [original_meta.message_id]
            msg["References"] = " ".join(refs)

        if outbound.attachments:
            body_part = MIMEMultipart("alternative")
            body_part.attach(MIMEText(plain_body, "plain", "utf-8"))
            body_part.attach(MIMEText(html_body, "html", "utf-8"))
            msg.attach(body_part)

            for att in outbound.attachments:
                maintype, _, subtype = att.content_type.partition("/")
                part = MIMEBase(maintype, subtype or "octet-stream")
                part.set_payload(att.data)
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    "attachment",
                    filename=att.filename,
                )
                msg.attach(part)
                log.info(
                    "Email вложение: %s (%d байт)",
                    att.filename,
                    att.size,
                )
        else:
            msg.attach(MIMEText(plain_body, "plain", "utf-8"))
            msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            if settings.smtp_use_ssl:
                with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port) as smtp:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                    smtp.send_message(msg)
            else:
                with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
                    if settings.smtp_use_tls:
                        smtp.starttls(context=ssl.create_default_context())
                    smtp.login(settings.smtp_user, settings.smtp_password)
                    smtp.send_message(msg)

            log.info("Email ответ отправлен: %s → %s", subject, original_meta.sender)
        except smtplib.SMTPException as e:
            log.error("SMTP error: %s", e)
