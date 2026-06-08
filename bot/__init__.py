"""1С Telegram Bot — Multi-Agent."""

from __future__ import annotations

import re
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


@lru_cache(maxsize=1)
def _read_version() -> str:
    try:
        return version("1c-odata-skill")
    except PackageNotFoundError:
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.MULTILINE)
        if match:
            return match.group(1)
        return "0.0.0"


__version__ = _read_version()
