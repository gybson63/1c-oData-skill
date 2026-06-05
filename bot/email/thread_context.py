#!/usr/bin/env python3
"""Построение и сжатие контекста email-цепочки для AI."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.email.store import ThreadMessage

log = logging.getLogger(__name__)


@dataclass
class ThreadContextConfig:
    """Настройки формирования контекста цепочки."""

    max_total_chars: int = 12000
    max_message_chars: int = 3000
    keep_full_recent: int = 3  # последние N писем — полностью
    keep_first: bool = True  # первое письмо (начало темы) — всегда
    middle_summary_chars: int = 300  # сжатие средних писем до N символов


def format_single_message(msg: ThreadMessage, max_chars: int | None = None) -> str:
    """Отформатировать одно письмо для AI-контекста."""
    lines = [
        f"От: {msg.meta.sender}",
        f"Дата: {msg.meta.date}",
        f"Тема: {msg.meta.subject}",
        "",
        msg.body,
    ]
    if msg.attachment_names:
        lines.append(f"\n[Вложения: {', '.join(msg.attachment_names)}]")
    if msg.attachment_text:
        lines.append(msg.attachment_text)

    text = "\n".join(lines)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars] + f"\n... (письмо обрезано, всего {len(text)} символов)"
    return text


def build_thread_context(
    thread: list[ThreadMessage],
    config: ThreadContextConfig | None = None,
) -> str:
    """Собрать контекст из всей цепочки писем.

    Стратегия:
      1. Если цепочка короткая — все письма полностью.
      2. Если длинная — первое + сжатые средние + последние N полностью.
    """
    if not thread:
        return ""

    cfg = config or ThreadContextConfig()

    if len(thread) == 1:
        return format_single_message(thread[0], cfg.max_message_chars)

    # Попробовать полный контекст
    full_parts = [format_single_message(m, cfg.max_message_chars) for m in thread]
    full_text = _join_messages(full_parts)

    if len(full_text) <= cfg.max_total_chars:
        return full_text

    # Сжатие
    log.info(
        "Сжатие контекста цепочки: %d писем, %d → %d символов",
        len(thread),
        len(full_text),
        cfg.max_total_chars,
    )
    return _compress_thread(thread, cfg)


def _join_messages(parts: list[str]) -> str:
    separator = "\n\n" + "—" * 40 + "\n\n"
    numbered = [f"--- Письмо {i + 1}/{len(parts)} ---\n{p}" for i, p in enumerate(parts)]
    return separator.join(numbered)


def _compress_thread(thread: list[ThreadMessage], cfg: ThreadContextConfig) -> str:
    n = len(thread)
    recent_start = max(0, n - cfg.keep_full_recent)

    parts: list[str] = []
    omitted = 0

    for i, msg in enumerate(thread):
        is_first = i == 0 and cfg.keep_first
        is_recent = i >= recent_start

        if is_first or is_recent:
            parts.append(format_single_message(msg, cfg.max_message_chars))
        else:
            # Сжатое представление среднего письма
            summary = format_single_message(msg, cfg.middle_summary_chars)
            parts.append(f"[... сжато ...]\n{summary}")
            omitted += 1

    result = _join_messages(parts)

    if omitted:
        header = f"[Контекст цепочки: {n} писем, {omitted} средних сжаты для экономии контекста]\n\n"
        result = header + result

    # Финальная обрезка если всё ещё слишком длинно
    if len(result) > cfg.max_total_chars:
        result = result[: cfg.max_total_chars] + "\n\n[... контекст обрезан ...]"

    return result


def build_user_text_from_thread(
    thread: list[ThreadMessage],
    config: ThreadContextConfig | None = None,
) -> tuple[str, str]:
    """Сформировать user_text и thread_context для InboundMessage.

    Returns:
        (latest_message_text, full_thread_context)
    """
    if not thread:
        return "", ""

    latest = thread[-1]
    latest_text = latest.body
    if latest.attachment_text:
        latest_text += latest.attachment_text

    thread_context = build_thread_context(thread, config)

    return latest_text, thread_context
