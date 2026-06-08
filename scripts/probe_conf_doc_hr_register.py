#!/usr/bin/env python3
"""Проверка conf-doc: InformationRegister.КадроваяИсторияСотрудников после обновления индекса."""

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
OT = "InformationRegister"
NAME = "КадроваяИсторияСотрудников"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "artifacts" / "conf_doc_hr_register_probe.json"


async def main() -> None:
    report: dict = {"searches": [], "ir_card": {}, "ir_chunks": [], "report_card": {}}

    async with httpx.AsyncClient(timeout=120) as client:
        health = await client.get(f"{CONF}/health")
        print(f"health: {health.status_code}")
        if not health.is_success:
            return

        for q, ot in [
            ("КадроваяИсторияСотрудников", None),
            ("КадроваяИсторияСотрудников", "InformationRegister"),
            ("сотрудники должность подразделение", None),
        ]:
            body: dict = {"query": q, "top_k": 5, "configuration": CFG, "include_fields": True}
            if ot:
                body["object_type"] = ot
            r = await client.post(f"{CONF}/search", json=body)
            hits = r.json() if r.is_success else []
            row = {
                "query": q,
                "object_type_filter": ot,
                "top": [
                    {
                        "type": h.get("object_type"),
                        "name": h.get("name"),
                        "score": round(float(h.get("score") or 0), 3),
                        "odata_fields_count": len(((h.get("odata_fields") or {}).get("fields")) or []),
                    }
                    for h in hits[:5]
                ],
            }
            report["searches"].append(row)
            print(f"\n=== search {q!r} type={ot} ===")
            for t in row["top"]:
                extra = f", fields={t['odata_fields_count']}" if t["odata_fields_count"] else ""
                print(f"  [{t['score']}] {t['type']}.{t['name']}{extra}")

        card = await client.get(f"{CONF}/objects/{OT}/{NAME}", params={"configuration": CFG})
        print(f"\n=== IR card HTTP {card.status_code} ===")
        if card.is_success:
            obj = card.json()
            o = obj.get("object", {})
            report["ir_card"] = {
                "synonym": o.get("synonym"),
                "attributes_count": obj.get("attributes_count"),
                "dimensions_count": obj.get("dimensions_count"),
                "resources_count": obj.get("resources_count"),
                "periodicity": o.get("periodicity"),
                "write_mode": o.get("write_mode"),
                "help_pages": obj.get("help_pages"),
                "chunks": [
                    {
                        "chunk_index": ch.get("chunk_index"),
                        "token_count": ch.get("token_count"),
                        "text_len": ch.get("text_len"),
                    }
                    for ch in obj.get("chunks", [])
                ],
            }
            print(json.dumps(report["ir_card"], ensure_ascii=False, indent=2))

            for ch in obj.get("chunks", []):
                idx = int(ch.get("chunk_index", 0))
                cr = await client.get(
                    f"{CONF}/objects/{OT}/{NAME}/chunks/{idx}",
                    params={"configuration": CFG},
                )
                if not cr.is_success:
                    continue
                text = cr.json().get("text", "")
                kind = "attributes"
                if "Измерен" in text or "Ресурс" in text:
                    kind = "structure"
                elif "Справка" in text or "Help" in text:
                    kind = "help"
                report["ir_chunks"].append({"chunk_index": idx, "kind": kind, "len": len(text), "preview": text[:500]})
                print(f"\n--- chunk {idx} [{kind}] len={len(text)} ---")
                print(text[:2000])
                if len(text) > 2000:
                    print("...[truncated]")

        # top-1 IR with full odata_fields
        sr = await client.post(
            f"{CONF}/search",
            json={
                "query": "КадроваяИсторияСотрудников",
                "top_k": 1,
                "object_type": "InformationRegister",
                "configuration": CFG,
                "include_fields": True,
            },
        )
        if sr.is_success and sr.json():
            h = sr.json()[0]
            fields = (h.get("odata_fields") or {}).get("fields") or []
            report["odata_fields"] = [
                {"name": f.get("name"), "type": f.get("type"), "title": f.get("title"), "kind": f.get("kind")}
                for f in fields
            ]
            print(f"\n=== odata_fields ({len(fields)}) ===")
            for f in fields:
                print(f"  [{f.get('kind')}] {f.get('name')}: {f.get('type')} — {f.get('title')}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved: {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
