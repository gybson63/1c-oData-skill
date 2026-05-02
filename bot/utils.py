#!/usr/bin/env python3
"""Общие утилиты бота."""

from __future__ import annotations

import asyncio
import logging
import json
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class RateLimiter:
    """Простой rate limiter для AI API (requests per minute)."""

    def __init__(self, rpm: int = 20) -> None:
        self._interval = 60.0 / max(rpm, 1)
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            import time
            now = time.monotonic()
            elapsed = now - self._last
            if elapsed < self._interval:
                await asyncio.sleep(self._interval - elapsed)
            self._last = time.monotonic()


def load_config(env_file: str, profile: str = "default") -> dict[str, Any]:
    """Загрузить конфигурацию из env.json.

    Структура env.json:
    {
      "profiles": {
        "default": { ... },
        "prod": { ... }
      }
    }
    """
    p = Path(env_file)
    if not p.exists():
        raise FileNotFoundError(f"Конфигурация не найдена: {env_file}")

    data = json.loads(p.read_text("utf-8"))
    profiles = data.get("profiles", {})
    if profile not in profiles:
        available = ", ".join(profiles.keys()) or "(нет)"
        raise ValueError(f"Профиль '{profile}' не найден. Доступные: {available}")

    return profiles[profile]


def esc_html(text: str) -> str:
    """Экранирование HTML-спецсимволов для Telegram."""
    return text.replace("&", "\x26amp;").replace("<", "\x26lt;").replace(">", "\x26gt;")


# Теги, поддерживаемые Telegram HTML
_TELEGRAM_TAGS = {"b", "i", "code", "pre", "a", "s", "u", "tg-spoiler", "blockquote", "tg-emoji"}


def sanitize_telegram_html(text: str) -> str:
    """Очистить HTML для Telegram: оставить только поддерживаемые теги,
    экранировать спецсимволы вне тегов.

    Telegram поддерживает: b, i, code, pre, a, s, u,
    tg-spoiler, blockquote, tg-emoji.
    """
    import html as _html
    import re

    # Разбить текст на «теги» и «текст между тегами»
    parts: list[str] = []
    last_end = 0

    for m in re.finditer(r"<(/?)(\w[\w-]*)([^>]*)>", text):
        # Текст перед тегом — экранируем через stdlib
        if m.start() > last_end:
            raw = text[last_end : m.start()]
            parts.append(_html.escape(raw))

        slash, tag, attrs = m.group(1), m.group(2).lower(), m.group(3)
        if tag in _TELEGRAM_TAGS:
            # Оставляем допустимый тег (с атрибутами или без)
            parts.append(f"<{slash}{tag}{attrs}>")
        # Недопустимый тег — просто убираем, содержимое сохранится

        last_end = m.end()

    # Хвост после последнего тега
    if last_end < len(text):
        parts.append(_html.escape(text[last_end:]))

    return "".join(parts)
