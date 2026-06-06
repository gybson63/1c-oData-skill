#!/usr/bin/env python3
"""Сводка по ошибкам из .cache/histories/."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path


def main() -> None:
    hist = Path(".cache/histories")
    files = list(hist.glob("*.json"))
    print(f"Total history files: {len(files)}")

    patterns = {
        "parse_failure": re.compile(r"разобрать\s+запрос|переформулировать", re.I),
        "odata_error": re.compile(r"Ошибка OData|❌", re.I),
        "unexpected": re.compile(r"непредвиденн", re.I),
        "raw_json": re.compile(r'^\s*\{[\s\S]*"entity"', re.M),
        "guard_reject": re.compile(r"не могу|не поддержива|отказ", re.I),
    }

    categories = Counter()
    odata_msgs = Counter()
    examples: dict[str, list] = defaultdict(list)
    multi_turn = 0
    single_turn = 0
    odata_segment = Counter()

    seg_re = re.compile(r"Сегмент пути (\S+) не найден", re.I)

    for p in sorted(files, key=lambda x: x.stat().st_mtime):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            categories["corrupt_json"] += 1
            continue

        if not isinstance(data, list) or len(data) < 2:
            categories["incomplete"] += 1
            continue

        if len(data) > 2:
            multi_turn += 1
        else:
            single_turn += 1

        query = data[0].get("content", "") if data else ""
        reply = data[-1].get("content", "") if len(data) >= 2 else ""

        matched = False
        for cat, pat in patterns.items():
            if pat.search(reply):
                categories[cat] += 1
                if len(examples[cat]) < 4:
                    examples[cat].append((query[:90], reply[:200], p.name))
                matched = True
                if cat == "odata_error":
                    m = seg_re.search(reply)
                    if m:
                        odata_segment[m.group(1)] += 1
                    if len(odata_msgs) < 20:
                        odata_msgs[reply[:120]] += 1
                break

        if not matched:
            if reply.strip() and not reply.strip().startswith("{"):
                categories["success"] += 1
            else:
                categories["other"] += 1
                if len(examples["other"]) < 3:
                    examples["other"].append((query[:90], reply[:200], p.name))

    print(f"Single-turn: {single_turn}, multi-turn: {multi_turn}\n")
    print("=== Categories ===")
    for k, v in categories.most_common():
        print(f"  {k}: {v} ({100 * v / len(files):.1f}%)")

    if odata_segment:
        print("\n=== OData missing segments (top) ===")
        for seg, cnt in odata_segment.most_common(15):
            print(f"  {seg}: {cnt}")

    for cat in [
        "parse_failure",
        "odata_error",
        "raw_json",
        "unexpected",
        "other",
        "success",
    ]:
        if examples.get(cat):
            print(f"\n--- {cat} examples ---")
            for q, r, fn in examples[cat]:
                print(f"  [{fn}]")
                print(f"    Q: {q!r}")
                print(f"    R: {r!r}")


if __name__ == "__main__":
    main()
