#!/usr/bin/env python3
"""Проверка conf-doc с запросами отчётов (SKD) для кейса остатков отпусков."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CONF = "http://localhost:8050"
CFG = "ЗарплатаИУправлениеПерсоналомКОРП"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "artifacts" / "conf_doc_vacation_report_probe.json"

SEARCH_QUERIES = [
    "остатки отпусков",
    "АналитикаОстатковОтпусков",
    "ОстаткиОтпусков",
    "ФактическиеОтпуска",
    "НачальныеОстаткиОтпусков",
]


async def search(client: httpx.AsyncClient, query: str, object_type: str | None = None) -> list[dict]:
    body: dict = {"query": query, "top_k": 8, "configuration": CFG, "include_fields": True}
    if object_type:
        body["object_type"] = object_type
    r = await client.post(f"{CONF}/search", json=body)
    return r.json() if r.is_success else []


def extract_registers_from_text(text: str) -> list[str]:
    import re

    patterns = [
        r"Регистр(?:Сведений|Накопления|Расчета)?\.(\w+)",
        r"InformationRegister\.(\w+)",
        r"AccumulationRegister\.(\w+)",
        r"CalculationRegister\.(\w+)",
    ]
    found: set[str] = set()
    for pat in patterns:
        found.update(re.findall(pat, text))
    return sorted(found)


async def main() -> None:
    report: dict = {"searches": [], "report_chunks": [], "registers_in_skd": []}

    async with httpx.AsyncClient(timeout=120) as client:
        health = await client.get(f"{CONF}/health")
        print(f"health: {health.status_code}")
        if not health.is_success:
            print(health.text)
            return

        for q in SEARCH_QUERIES:
            hits = await search(client, q)
            row = {
                "query": q,
                "top": [
                    {
                        "type": h.get("object_type"),
                        "name": h.get("name"),
                        "score": round(float(h.get("score") or 0), 3),
                    }
                    for h in hits[:5]
                ],
            }
            report["searches"].append(row)
            print(f"\n=== search: {q!r} ===")
            for h in row["top"]:
                print(f"  [{h['score']}] {h['type']}.{h['name']}")

        # Report drill-down
        ot, name = "Report", "ОстаткиОтпусков"
        card = await client.get(f"{CONF}/objects/{ot}/{name}", params={"configuration": CFG})
        print(f"\n=== Report.{name} card: HTTP {card.status_code} ===")
        if not card.is_success:
            print(card.text[:300])
        else:
            obj = card.json()
            chunks = obj.get("chunks", [])
            print(f"  chunks: {len(chunks)}")
            for ch in chunks:
                idx = ch.get("chunk_index", 0)
                (ch.get("text_preview") or "")[:80]
                cr = await client.get(
                    f"{CONF}/objects/{ot}/{name}/chunks/{idx}",
                    params={"configuration": CFG},
                )
                if not cr.is_success:
                    continue
                text = cr.json().get("text", "")
                kind = "other"
                if "Запрос СКД" in text:
                    kind = "skd_query"
                elif "Модуль объекта" in text:
                    kind = "module"
                elif "Реквизит" in text or "| Имя |" in text:
                    kind = "attributes"
                regs = extract_registers_from_text(text)
                if regs:
                    report["registers_in_skd"].extend(regs)
                preview = text[:400].replace("\n", " ")
                report["report_chunks"].append(
                    {
                        "chunk_index": idx,
                        "kind": kind,
                        "registers": regs,
                        "preview": preview,
                        "len": len(text),
                    }
                )
                print(f"\n  chunk {idx} [{kind}] len={len(text)}")
                if kind == "skd_query":
                    print(f"    registers: {', '.join(regs[:12])}")
                    print(f"    {preview[:200]}…")

        report["registers_in_skd"] = sorted(set(report["registers_in_skd"]))
        print("\n=== registers mentioned in Report SKD chunks ===")
        for r in report["registers_in_skd"]:
            print(f"  {r}")

        # search with object_type Report
        hits = await search(client, "остатки отпусков", "Report")
        print("\n=== search 'остатки отпусков' object_type=Report ===")
        for h in hits[:3]:
            print(f"  [{h.get('score', 0):.3f}] {h.get('object_type')}.{h.get('name')}")
            if h.get("odata_fields"):
                print("    odata_fields: present")
            text = (h.get("text") or "")[:300]
            if "Запрос СКД" in text or "Регистр" in text:
                print(f"    text snippet: {text[:200]}…")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport saved: {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
