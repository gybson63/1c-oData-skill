#!/usr/bin/env python3
"""Сводка по ошибкам разбора Step 1 из logs/parse_failures.jsonl и step1-артефактов."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def _load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _scan_session_logs(log_dir: Path) -> list[dict]:
    hits: list[dict] = []
    for log_file in log_dir.glob("*.log"):
        text = log_file.read_text(encoding="utf-8", errors="replace")
        if "Не удалось извлечь JSON" in text or "step1_parse_failure" in text:
            hits.append({"log_file": str(log_file), "has_parse_failure": True})
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze Step 1 parse failures")
    parser.add_argument("--log-dir", default="logs", help="Logs directory")
    parser.add_argument("--limit", type=int, default=50, help="Max records to print")
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    journal_path = log_dir / "parse_failures.jsonl"
    records = _load_jsonl(journal_path)

    print(f"=== parse_failures.jsonl ({journal_path}) ===")
    print(f"Total records: {len(records)}")
    if records:
        reasons = Counter(r.get("failure_reason", "unknown") for r in records)
        print("By reason:", dict(reasons))
        print()
        for rec in records[-args.limit :]:
            print(
                f"{rec.get('ts', '?')} | {rec.get('failure_reason', '?')} | "
                f"channel={rec.get('channel', '')} | query={rec.get('user_query', '')[:80]!r}"
            )
            snippet = (rec.get("ai_response_snippet") or "")[:120]
            if snippet:
                print(f"  ai: {snippet!r}")
            artifact = rec.get("step1_artifact")
            if artifact:
                print(f"  step1: {artifact}")
            print()

    session_hits = _scan_session_logs(log_dir)
    if session_hits:
        print(f"=== session .log files with parse failure markers: {len(session_hits)} ===")
        for hit in session_hits[:10]:
            print(f"  {hit['log_file']}")

    step1_files = sorted(log_dir.glob("*/*_step1.json"))
    if step1_files:
        print(f"\n=== step1 artifacts: {len(step1_files)} files in session subdirs ===")
        for path in step1_files[-5:]:
            print(f"  {path}")

    return 0 if records or session_hits or step1_files else 1


if __name__ == "__main__":
    sys.exit(main())
