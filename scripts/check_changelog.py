#!/usr/bin/env python3
"""Pre-commit: требовать CHANGELOG.md при изменениях в коде приложения."""

from __future__ import annotations

import subprocess
import sys

CODE_PREFIXES = ("bot/", "bot_lib/", "mcp_servers/")


def _staged_files() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    files = _staged_files()
    code_changes = [path for path in files if path.startswith(CODE_PREFIXES)]
    if not code_changes:
        return 0
    if "CHANGELOG.md" in files:
        return 0
    print(
        "Changes in bot/, bot_lib/, or mcp_servers/ require CHANGELOG.md update "
        "in the same commit (section [Unreleased]).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
