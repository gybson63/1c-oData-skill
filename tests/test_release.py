#!/usr/bin/env python3
"""Tests for scripts/release.py helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RELEASE_PATH = ROOT / "scripts" / "release.py"


def _load_release_module():
    spec = importlib.util.spec_from_file_location("release", RELEASE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(name="release")
def release_fixture():
    return _load_release_module()


def test_extract_unreleased(release) -> None:
    content = """## [Unreleased]

### Added
- New feature

## [0.1.0] - 2026-01-01
"""
    body = release.extract_unreleased(content)
    assert "- New feature" in body


def test_has_meaningful_entries(release) -> None:
    assert release._has_meaningful_entries("### Added\n\n- item\n")
    assert not release._has_meaningful_entries("### Added\n\n")


def test_extract_release_section(release) -> None:
    content = """## [0.2.0] - 2026-06-01

### Fixed
- Bug

## [0.1.0] - 2026-01-01
"""
    section = release.extract_release_section(content, "0.2.0")
    assert "- Bug" in section
