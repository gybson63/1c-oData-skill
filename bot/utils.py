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