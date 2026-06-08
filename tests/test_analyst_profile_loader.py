#!/usr/bin/env python3
"""Тесты profile_loader аналитика."""

from pathlib import Path

from bot.agents.analyst.profile_loader import format_profile_block, load_profile

ROOT = Path(__file__).resolve().parent.parent
ZUP_PROFILE = ROOT / "skills" / "analyst" / "profiles" / "zup-korp.md"


def test_load_zup_profile():
    text = load_profile(ZUP_PROFILE)
    assert "ЗарплатаИУправлениеПерсоналомКОРП" in text
    assert "АналитикаОстатковОтпусков" in text


def test_format_profile_block():
    block = format_profile_block(ZUP_PROFILE)
    assert "PROFILE КОНФИГУРАЦИИ" in block
    assert "АналитикаОстатковОтпусков" in block


def test_missing_profile_returns_empty():
    missing = ROOT / "skills" / "analyst" / "profiles" / "nonexistent.md"
    assert load_profile(missing) == ""
