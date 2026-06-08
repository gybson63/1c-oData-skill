#!/usr/bin/env python3
"""Загрузка profile MD аналитика."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_PROFILE = _PROJECT_ROOT / "skills" / "analyst" / "profiles" / "zup-korp.md"
_cached_profile: str | None = None
_cached_path: Path | None = None


def load_profile(path: str | Path | None = None) -> str:
    """Прочитать profile MD (с кэшем на путь)."""
    global _cached_profile, _cached_path

    profile_path = Path(path) if path else _DEFAULT_PROFILE
    if not profile_path.is_absolute():
        profile_path = _PROJECT_ROOT / profile_path

    if _cached_profile is not None and _cached_path == profile_path:
        return _cached_profile

    if not profile_path.is_file():
        log.debug("analyst profile not found: %s", profile_path)
        _cached_profile = ""
        _cached_path = profile_path
        return ""

    text = profile_path.read_text(encoding="utf-8").strip()
    _cached_profile = text
    _cached_path = profile_path
    log.info("analyst profile loaded: %s (%d chars)", profile_path, len(text))
    return text


def format_profile_block(path: str | Path | None = None) -> str:
    """Блок profile для system prompt аналитика."""
    profile = load_profile(path)
    if not profile:
        return ""
    return f"\n\n--- PROFILE КОНФИГУРАЦИИ 1С ---\n{profile}\n--- КОНЕЦ PROFILE ---\n"
