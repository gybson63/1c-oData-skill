#!/usr/bin/env python3
"""Тесты промптов analytics."""

from __future__ import annotations

from bot.agents.odata.prompts import ODATA_REFERENCE, STEP1_SYSTEM, STEP2_SYSTEM


def test_step1_contains_analytics_mode():
    assert "mode=analytics" in STEP1_SYSTEM
    assert "mode=query" in STEP1_SYSTEM
    assert "график" in STEP1_SYSTEM.lower()


def test_step1_proactive_chart_rules():
    assert "сам добавить chart" in STEP1_SYSTEM or "МОЖЕШЬ сам добавить chart" in STEP1_SYSTEM
    assert "pie" in STEP1_SYSTEM


def test_analytics_reference_topic():
    assert "analytics" in ODATA_REFERENCE
    assert "bar" in ODATA_REFERENCE["analytics"]
    assert "pie" in ODATA_REFERENCE["analytics"]
    assert "joins" in ODATA_REFERENCE["analytics"]


def test_step2_chart_hint():
    assert "график" in STEP2_SYSTEM.lower()
