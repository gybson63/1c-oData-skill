"""Harness для отправки и приёма email в интеграционных тестах.

Поддерживает:
- SMTP-отправку (stdlib smtplib)
- ожидание ответа через IMAP (GreenMail) или MailHog HTTP API
- разбор MIME-ответа для assert
"""

from __future__ import annotations

import email
import imaplib
import logging
import re
import smtplib
import time
import uuid
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "artifacts"


@dataclass
class SmtpConfig:
    host: str = "localhost"
    port: int = 3025
    user: str = ""
    password: str = ""
    use_ssl: bool = False
    use_tls: bool = False


@dataclass
class ImapConfig:
    host: str = "localhost"
    port: int = 3143
    user: str = ""
    password: str = ""
    folder: str = "INBOX"
    use_ssl: bool = False


@dataclass
class ParsedReply:
    subject: str = ""
    plain: str = ""
    html: str = ""
    from_addr: str = ""
    message_id: str = ""
    in_reply_to: str = ""
    references: str = ""
    attachments: list[tuple[str, bytes]] = field(default_factory=list)
    raw: bytes = b""


def unique_subject(prefix: str = "odata-test") -> str:
    """Уникальная тема письма для изоляции тестов."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def send_email(
    *,
    smtp: SmtpConfig,
    to_addr: str,
    subject: str,
    body: str,
    from_addr: str = "tester@local.test",
    from_name: str = "Tester",
    in_reply_to: str = "",
    references: str = "",
    attachments: list[tuple[str, str, bytes]] | None = None,
) -> str:
    """Отправить письмо по SMTP. Возвращает Message-ID."""
    message_id = f"<{uuid.uuid4().hex}@test.local>"

    if attachments:
        msg: email.message.Message = MIMEMultipart("mixed")
        msg.attach(MIMEText(body, "plain", "utf-8"))
        for filename, content_type, data in attachments:
            maintype, _, subtype = content_type.partition("/")
            part = email.mime.base.MIMEBase(maintype, subtype or "octet-stream")
            part.set_payload(data)
            email.encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=filename)
            msg.attach(part)
    else:
        msg = MIMEText(body, "plain", "utf-8")

    msg["Subject"] = subject
    msg["From"] = formataddr((from_name, from_addr))
    msg["To"] = to_addr
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    if smtp.use_ssl:
        with smtplib.SMTP_SSL(smtp.host, smtp.port) as client:
            if smtp.user and smtp.password:
                client.login(smtp.user, smtp.password)
            client.send_message(msg)
    else:
        with smtplib.SMTP(smtp.host, smtp.port) as client:
            if smtp.use_tls:
                client.starttls()
            if smtp.user and smtp.password:
                client.login(smtp.user, smtp.password)
            client.send_message(msg)

    log.debug("Sent email %s → %s subject=%s", from_addr, to_addr, subject)
    return message_id


def parse_reply_mime(raw: bytes) -> ParsedReply:
    """Разобрать MIME-ответ для assert в тестах."""
    msg = email.message_from_bytes(raw)
    plain_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[tuple[str, bytes]] = []

    if msg.is_multipart():
        for part in msg.walk():
            disposition = part.get("Content-Disposition", "")
            if "attachment" in disposition:
                filename = part.get_filename() or "attachment"
                payload = part.get_payload(decode=True) or b""
                attachments.append((filename, payload))
                continue
            ctype = part.get_content_type()
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            if ctype == "text/plain":
                plain_parts.append(text)
            elif ctype == "text/html":
                html_parts.append(text)
    else:
        payload = msg.get_payload(decode=True) or b""
        text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
        if msg.get_content_type() == "text/html":
            html_parts.append(text)
        else:
            plain_parts.append(text)

    _, from_addr = parseaddr(msg.get("From", ""))
    return ParsedReply(
        subject=msg.get("Subject", ""),
        plain="\n".join(plain_parts),
        html="\n".join(html_parts),
        from_addr=from_addr,
        message_id=msg.get("Message-ID", ""),
        in_reply_to=msg.get("In-Reply-To", ""),
        references=msg.get("References", ""),
        attachments=attachments,
        raw=raw,
    )


def imap_max_uid(imap: ImapConfig) -> int:
    """Текущий максимальный UID в ящике (0 если пусто)."""
    if imap.use_ssl:
        client = imaplib.IMAP4_SSL(imap.host, imap.port)
    else:
        client = imaplib.IMAP4(imap.host, imap.port)
    try:
        client.login(imap.user, imap.password)
        client.select(imap.folder)
        status, data = client.search(None, "ALL")
        if status != "OK" or not data or not data[0]:
            return 0
        uids = [int(u) for u in data[0].split()]
        return max(uids) if uids else 0
    finally:
        try:
            client.logout()
        except Exception:
            pass


def _imap_fetch_matching(
    imap: ImapConfig,
    *,
    subject_contains: str,
    from_contains: str = "",
    since_uid: int = 0,
) -> list[tuple[int, bytes]]:
    """Найти письма по подстроке в теме."""
    if imap.use_ssl:
        client = imaplib.IMAP4_SSL(imap.host, imap.port)
    else:
        client = imaplib.IMAP4(imap.host, imap.port)

    try:
        client.login(imap.user, imap.password)
        client.select(imap.folder)
        status, data = client.search(None, "ALL")
        if status != "OK" or not data or not data[0]:
            return []

        matches: list[tuple[int, bytes]] = []
        for uid_b in data[0].split():
            uid = int(uid_b)
            if uid <= since_uid:
                continue
            status, fetched = client.fetch(uid_b, "(RFC822)")
            if status != "OK" or not fetched or not fetched[0]:
                continue
            raw = fetched[0][1]
            if not isinstance(raw, bytes):
                continue
            parsed = parse_reply_mime(raw)
            if subject_contains.lower() not in parsed.subject.lower():
                continue
            if from_contains and from_contains.lower() not in parsed.from_addr.lower():
                continue
            matches.append((uid, raw))
        return matches
    finally:
        try:
            client.logout()
        except Exception:
            pass


def wait_for_reply_imap(
    imap: ImapConfig,
    *,
    subject_contains: str,
    timeout: float = 180.0,
    poll_interval: float = 2.0,
    from_contains: str = "",
    since_uid: int = 0,
) -> ParsedReply:
    """Ожидать ответ по IMAP (GreenMail и др.)."""
    deadline = time.monotonic() + timeout
    last_uid = 0
    while time.monotonic() < deadline:
        matches = _imap_fetch_matching(
            imap,
            subject_contains=subject_contains,
            from_contains=from_contains,
            since_uid=max(since_uid, last_uid),
        )
        if matches:
            _, raw = matches[-1]
            return parse_reply_mime(raw)
        time.sleep(poll_interval)
    raise TimeoutError(f"No reply with subject containing {subject_contains!r} within {timeout}s")


def wait_for_reply_mailhog(
    api_base: str,
    *,
    subject_contains: str,
    timeout: float = 180.0,
    poll_interval: float = 2.0,
    to_contains: str = "",
) -> ParsedReply:
    """Ожидать ответ через MailHog HTTP API (/api/v2/messages)."""
    api_base = api_base.rstrip("/")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{api_base}/api/v2/messages")
            resp.raise_for_status()
            payload = resp.json()
        items: list[dict[str, Any]] = payload.get("items", [])
        for item in reversed(items):
            content = item.get("Content", {})
            headers = content.get("Headers", {})
            subject_list = headers.get("Subject", [])
            subject = subject_list[0] if subject_list else ""
            if subject_contains.lower() not in subject.lower():
                continue
            to_list = headers.get("To", [])
            to_header = to_list[0] if to_list else ""
            if to_contains and to_contains.lower() not in to_header.lower():
                continue
            body_b64 = content.get("Body", "")
            raw = body_b64.encode("utf-8") if isinstance(body_b64, str) else body_b64
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            # MailHog API returns body only; rebuild minimal message for parser
            mime = (
                f"Subject: {subject}\r\nFrom: {headers.get('From', [''])[0]}\r\n"
                f"Content-Type: text/plain\r\n\r\n{content.get('Body', '')}"
            ).encode()
            return parse_reply_mime(mime)
        time.sleep(poll_interval)
    raise TimeoutError(f"No MailHog message with subject {subject_contains!r} within {timeout}s")


def wait_for_reply(
    *,
    subject_contains: str,
    timeout: float = 180.0,
    poll_interval: float = 2.0,
    imap: ImapConfig | None = None,
    mailhog_api: str | None = None,
    from_contains: str = "",
    to_contains: str = "",
    since_uid: int = 0,
) -> ParsedReply:
    """Универсальное ожидание ответа: IMAP или MailHog API."""
    if imap is not None:
        return wait_for_reply_imap(
            imap,
            subject_contains=subject_contains,
            timeout=timeout,
            poll_interval=poll_interval,
            from_contains=from_contains,
            since_uid=since_uid,
        )
    if mailhog_api:
        return wait_for_reply_mailhog(
            mailhog_api,
            subject_contains=subject_contains,
            timeout=timeout,
            poll_interval=poll_interval,
            to_contains=to_contains,
        )
    raise ValueError("Either imap or mailhog_api must be provided")


def assert_no_error(body: str) -> None:
    """Структурная проверка: ответ не содержит явной ошибки."""
    lowered = body.lower()
    assert "traceback" not in lowered
    assert "непредвиденная ошибка" not in lowered or "⚠️" not in body


def assert_reply_in_thread(reply: ParsedReply, original_message_id: str) -> None:
    """Проверить, что ответ — часть цепочки."""
    assert "re:" in reply.subject.lower()
    combined = f"{reply.in_reply_to} {reply.references}"
    needle = original_message_id.strip()
    assert needle in combined or needle.strip("<>") in combined


def save_artifact(name: str, data: bytes, *, suffix: str = ".eml") -> Path:
    """Сохранить MIME при падении теста для отладки."""
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS_DIR / f"{name}{suffix}"
    path.write_bytes(data)
    return path


def body_text(reply: ParsedReply) -> str:
    """Текст ответа: plain или html без тегов."""
    if reply.plain.strip():
        return reply.plain
    return re.sub(r"<[^>]+>", " ", reply.html)
