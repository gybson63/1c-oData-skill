#!/usr/bin/env python3
"""Загрузка config_hint.md для промпта Step1."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

_DEFAULT_HINT = Path(__file__).resolve().parent.parent.parent / "config_hint.md"
_cached_hint: str | None = None
_cached_path: Path | None = None


def load_config_hint(path: str | Path | None = None) -> str:
    """Прочитать config_hint.md (с кэшем на путь)."""
    global _cached_hint, _cached_path

    hint_path = Path(path) if path else _DEFAULT_HINT
    if _cached_hint is not None and _cached_path == hint_path:
        return _cached_hint

    if not hint_path.is_file():
        log.debug("config_hint not found: %s", hint_path)
        _cached_hint = ""
        _cached_path = hint_path
        return ""

    text = hint_path.read_text(encoding="utf-8").strip()
    _cached_hint = text
    _cached_path = hint_path
    log.info("config_hint loaded: %s (%d chars)", hint_path, len(text))
    return text


def format_config_hint_block(path: str | Path | None = None) -> str:
    """Блок для добавления в system prompt Step1."""
    hint = load_config_hint(path)
    if not hint:
        return ""
    return f"\n\n--- СПРАВКА ПО КОНФИГУРАЦИИ 1С (config_hint) ---\n{hint}\n--- КОНЕЦ СПРАВКИ ---\n"
