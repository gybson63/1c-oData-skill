#!/usr/bin/env python3
"""Подготовка email-ответа с вложением при превышении лимита inline-текста."""

from __future__ import annotations

import html
import re
from datetime import datetime

from bot.config import EmailSettings
from bot.email.formatter import format_email_reply
from bot.messages import Attachment


def _strip_html(text: str) -> str:
    plain = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    plain = re.sub(r"<[^>]+>", "", plain)
    return html.unescape(plain).strip()


def _make_filename(settings: EmailSettings, subject: str = "") -> str:
    if settings.attachment_filename:
        return settings.attachment_filename

    base = "odata-result"
    if subject:
        safe = re.sub(r"[^\w\s-]", "", subject, flags=re.UNICODE)[:40].strip()
        if safe:
            base = safe.replace(" ", "-")
    date = datetime.now().strftime("%Y%m%d")
    ext = settings.attachment_format.lstrip(".")
    return f"{base}-{date}.{ext}"


def prepare_email_response(
    answer: str,
    settings: EmailSettings,
    *,
    subject: str = "",
    token_footer: str = "",
) -> tuple[str, list[Attachment]]:
    """Разделить ответ на inline-тело и вложение при необходимости.

    Если ``len(answer) <= inline_max_chars`` — весь ответ в теле письма.
    Иначе — краткое сообщение в теле, полный результат во вложении.

    Returns:
        (body_for_email, attachments)
    """
    if len(answer) <= settings.inline_max_chars:
        return answer, []

    filename = _make_filename(settings, subject)
    full_html, _ = format_email_reply(answer, token_footer=token_footer)

    preview_len = min(settings.inline_preview_chars, settings.inline_max_chars)
    plain = _strip_html(answer)
    preview = plain[:preview_len]
    if len(plain) > preview_len:
        preview += "…"

    notice = (
        f"<p>Результат запроса слишком большой для отображения в письме "
        f"({len(plain):,} символов).</p>"
        f"<p><strong>Полный ответ — во вложении «{html.escape(filename)}».</strong></p>"
    )
    if preview:
        notice += f"<h3>Краткий просмотр</h3><pre>{html.escape(preview)}</pre>"

    att_data = full_html.encode("utf-8")
    content_type = "text/html" if settings.attachment_format == "html" else f"text/{settings.attachment_format}"

    attachment = Attachment(
        filename=filename,
        content_type=content_type,
        data=att_data,
    )

    return notice, [attachment]
