#!/usr/bin/env python3
"""Заголовок ответа на основе краткой формулировки запроса."""

from __future__ import annotations

import re

from bot.agents.odata.analytics_models import RequestBrief
from bot.utils import esc_html

_LEADING_TITLE_RE = re.compile(r"^\s*<b>[^<]*</b>\s*", re.I)


def format_request_headline(brief: RequestBrief) -> str:
    """HTML-строка заголовка для начала ответа."""
    emoji = (brief.emoji or "📋").strip()
    return f"<b>{emoji} {esc_html(brief.headline)}</b>"


def strip_leading_headline(answer: str) -> str:
    """Убрать первый жирный заголовок, если он уже есть в теле ответа."""
    if not answer:
        return answer
    return _LEADING_TITLE_RE.sub("", answer, count=1).lstrip()


def apply_request_headline(answer: str, brief: RequestBrief | None) -> str:
    """Поставить краткую формулировку запроса в начало ответа."""
    if not brief or not brief.headline:
        return answer
    body = strip_leading_headline(answer)
    headline = format_request_headline(brief)
    if not body:
        return headline
    return f"{headline}\n{body}"
