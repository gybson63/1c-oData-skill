#!/usr/bin/env python3
"""Парсинг email-сообщений: тело, HTML, цитаты, заголовки."""

from __future__ import annotations

import email
import logging
import re
from email.header import decode_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from bot.messages import Attachment, EmailMessageMeta

log = logging.getLogger(__name__)


def _payload_as_bytes(payload: Any) -> bytes | None:
    """Привести результат get_payload(decode=True) к bytes."""
    if payload is None:
        return None
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return None


def _decode_payload_bytes(payload: bytes, charset: str) -> str:
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


# Паттерны начала блока цитирования (рус/англ)
_QUOTE_PATTERNS = [
    re.compile(r"^On .+ wrote:$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^[-—–]{2,}\s*Original Message\s*[-—–]{2,}", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^[-—–]{2,}\s*Исходное сообщение\s*[-—–]{2,}", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^От:\s*.+\nОтправлено:\s*.+\nКому:", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^From:\s*.+\nSent:\s*.+\nTo:", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^_{5,}$", re.MULTILINE),
    re.compile(r"^>{1,}\s", re.MULTILINE),
]

# Блочные HTML-теги, которые сохраняем при конвертации
_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "hr"}


def decode_mime_header(value: str | None) -> str:
    """Декодировать MIME-encoded заголовок."""
    if not value:
        return ""
    parts: list[str] = []
    for fragment, charset in decode_header(value):
        if isinstance(fragment, bytes):
            parts.append(fragment.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(fragment)
    return " ".join(parts).strip()


def html_to_text(html: str) -> str:
    """Конвертировать HTML в читаемый plain text с сохранением структуры."""
    if not html:
        return ""

    text = html
    # Удалить style/script
    text = re.sub(r"<(style|script)[^>]*>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Блочные теги → переносы
    for tag in _BLOCK_TAGS:
        text = re.sub(rf"</{tag}>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(rf"<{tag}[^>]*>", "\n", text, flags=re.IGNORECASE)
    # Таблицы: ячейки через |
    text = re.sub(r"</td>", " | ", text, flags=re.IGNORECASE)
    text = re.sub(r"</th>", " | ", text, flags=re.IGNORECASE)
    # Списки
    text = re.sub(r"<li[^>]*>", "• ", text, flags=re.IGNORECASE)
    # Жирный/курсив — оставляем маркеры
    text = re.sub(r"<(strong|b)[^>]*>(.*?)</\1>", r"**\2**", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<(em|i)[^>]*>(.*?)</\1>", r"_\2_", text, flags=re.DOTALL | re.IGNORECASE)
    # Ссылки: текст (url)
    text = re.sub(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        r"\2 (\1)",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Остальные теги
    text = re.sub(r"<[^>]+>", "", text)
    # HTML entities
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    # Нормализация пробелов
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_quoted_reply(text: str) -> str:
    """Убрать цитируемую часть переписки, оставить только новый текст."""
    if not text:
        return ""

    # Gmail/Outlook style: строки с >
    lines = text.split("\n")
    clean_lines: list[str] = []
    for line in lines:
        if line.startswith(">"):
            break
        clean_lines.append(line)
    candidate = "\n".join(clean_lines).strip()

    # Паттерны блоков цитирования
    earliest = len(candidate)
    for pattern in _QUOTE_PATTERNS:
        match = pattern.search(candidate)
        if match and match.start() < earliest:
            earliest = match.start()

    if earliest < len(candidate):
        candidate = candidate[:earliest].strip()

    return candidate or text.strip()


def extract_body(msg: Message) -> tuple[str, str]:
    """Извлечь plain text и HTML из MIME-сообщения.

    Returns:
        (plain_text, html_body)
    """
    plain_parts: list[str] = []
    html_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in disposition:
                continue
            raw_payload = _payload_as_bytes(part.get_payload(decode=True))
            if raw_payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            decoded = _decode_payload_bytes(raw_payload, charset)

            if content_type == "text/plain":
                plain_parts.append(decoded)
            elif content_type == "text/html":
                html_parts.append(decoded)
    else:
        raw_payload = _payload_as_bytes(msg.get_payload(decode=True))
        if raw_payload:
            charset = msg.get_content_charset() or "utf-8"
            decoded = _decode_payload_bytes(raw_payload, charset)
            if msg.get_content_type() == "text/html":
                html_parts.append(decoded)
            else:
                plain_parts.append(decoded)

    plain = "\n".join(plain_parts).strip()
    html = "\n".join(html_parts).strip()

    # Если plain пустой — конвертируем HTML
    if not plain and html:
        plain = html_to_text(html)

    return plain, html


def extract_attachments(msg: Message) -> list[Attachment]:
    """Извлечь вложения из MIME-сообщения."""
    attachments: list[Attachment] = []

    for part in msg.walk():
        disposition = str(part.get("Content-Disposition", ""))
        if "attachment" not in disposition and not part.get_filename():
            continue

        filename = part.get_filename()
        if filename:
            filename = decode_mime_header(filename)
        else:
            filename = "attachment"

        raw_payload = _payload_as_bytes(part.get_payload(decode=True))
        if raw_payload is None:
            continue

        attachments.append(
            Attachment(
                filename=filename,
                content_type=part.get_content_type(),
                data=raw_payload,
            )
        )

    return attachments


def parse_references(header_value: str | None) -> list[str]:
    """Разобрать заголовок References/In-Reply-To в список Message-ID."""
    if not header_value:
        return []
    return re.findall(r"<[^>]+>", header_value)


def compute_thread_id(msg: Message) -> str:
    """Определить ID цепочки по References / In-Reply-To / Message-ID."""
    references = parse_references(msg.get("References"))
    in_reply_to = msg.get("In-Reply-To", "").strip()
    message_id = msg.get("Message-ID", "").strip()

    if references:
        return references[0]
    if in_reply_to:
        return in_reply_to
    return message_id or "unknown"


def parse_email_message(raw_bytes: bytes) -> tuple[EmailMessageMeta, str, str, list[Attachment]]:
    """Полный разбор входящего письма.

    Returns:
        (meta, plain_body, html_body, attachments)
    """
    msg = email.message_from_bytes(raw_bytes)

    message_id = msg.get("Message-ID", "").strip()
    in_reply_to = msg.get("In-Reply-To", "").strip()
    references = parse_references(msg.get("References"))
    subject = decode_mime_header(msg.get("Subject"))
    sender_name, sender_addr = parseaddr(decode_mime_header(msg.get("From")))
    sender = f"{sender_name} <{sender_addr}>" if sender_name else sender_addr

    to_header = decode_mime_header(msg.get("To"))
    recipients = [addr.strip() for _, addr in email.utils.getaddresses([to_header]) if addr]

    date_str = ""
    date_header = msg.get("Date")
    if date_header:
        try:
            dt = parsedate_to_datetime(date_header)
            date_str = dt.isoformat()
        except (ValueError, TypeError):
            date_str = date_header

    thread_id = compute_thread_id(msg)

    plain, html = extract_body(msg)
    plain = strip_quoted_reply(plain)
    attachments = extract_attachments(msg)

    meta = EmailMessageMeta(
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
        subject=subject,
        sender=sender,
        recipients=recipients,
        date=date_str,
        thread_id=thread_id,
    )

    return meta, plain, html, attachments
