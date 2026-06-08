#!/usr/bin/env python3
"""Release helper: check, prepare, notes."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
PYPROJECT = ROOT / "pyproject.toml"

UNRELEASED_TEMPLATE = """## [Unreleased]

### Added

### Changed

### Fixed

### Removed

"""


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def get_version() -> str:
    match = re.search(r'^version\s*=\s*"([^"]+)"', _read(PYPROJECT), re.MULTILINE)
    if not match:
        raise SystemExit("version not found in pyproject.toml")
    return match.group(1)


def set_version(version: str) -> None:
    text = _read(PYPROJECT)
    new_text, count = re.subn(
        r'^(version\s*=\s*")([^"]+)(")',
        rf"\g<1>{version}\3",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise SystemExit("failed to update version in pyproject.toml")
    _write(PYPROJECT, new_text)


def extract_unreleased(content: str) -> str:
    match = re.search(r"## \[Unreleased\]\s*\n(.*?)(?=\n## \[|\Z)", content, re.DOTALL)
    return match.group(1).strip() if match else ""


def extract_release_section(content: str, version: str) -> str:
    pattern = rf"## \[{re.escape(version)}\][^\n]*\n(.*?)(?=\n## \[|\Z)"
    match = re.search(pattern, content, re.DOTALL)
    return match.group(1).strip() if match else ""


def _has_meaningful_entries(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            return True
    return False


def cmd_check() -> int:
    unreleased = extract_unreleased(_read(CHANGELOG))
    if not _has_meaningful_entries(unreleased):
        print("[Unreleased] has no bullet entries — add changelog items before release.", file=sys.stderr)
        return 1
    print(f"Current version: {get_version()}")
    print("Release check OK")
    return 0


def cmd_prepare(version: str) -> int:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        print("Version must be SemVer X.Y.Z", file=sys.stderr)
        return 1

    content = _read(CHANGELOG)
    unreleased = extract_unreleased(content)
    if not _has_meaningful_entries(unreleased):
        print("[Unreleased] has no bullet entries", file=sys.stderr)
        return 1

    today = date.today().isoformat()
    new_version_block = f"## [{version}] - {today}\n\n{unreleased}\n\n"
    updated = re.sub(
        r"## \[Unreleased\]\s*\n.*?(?=\n## \[|\Z)",
        UNRELEASED_TEMPLATE + new_version_block,
        content,
        count=1,
        flags=re.DOTALL,
    )
    if updated == content:
        print("Failed to update CHANGELOG.md", file=sys.stderr)
        return 1

    _write(CHANGELOG, updated)
    set_version(version)
    print(f"Prepared release {version}")
    return 0


def cmd_notes(version: str) -> int:
    section = extract_release_section(_read(CHANGELOG), version)
    if not section:
        print(f"Section [{version}] not found in CHANGELOG.md", file=sys.stderr)
        return 1
    print(section)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Release helper for 1c-oData-skill")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="Verify [Unreleased] has entries")

    prepare = sub.add_parser("prepare", help="Move [Unreleased] to version section")
    prepare.add_argument("version", help="SemVer, e.g. 0.2.0")

    notes = sub.add_parser("notes", help="Print release notes for a version")
    notes.add_argument("version", help="SemVer, e.g. 0.2.0")

    args = parser.parse_args(argv)
    if args.command == "check":
        return cmd_check()
    if args.command == "prepare":
        return cmd_prepare(args.version)
    if args.command == "notes":
        return cmd_notes(args.version)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
