#!/usr/bin/env python3
"""Deep probe: vacation OData entities + conf-doc Document.Отпуск movements."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
ODATA = "http://localhost/zup_gazaliev/odata/standard.odata"
CONF_DOC = "http://localhost:8050"
CONF = "ЗарплатаИУправлениеПерсоналомКОРП"
AUTH = ("АдминБит", "АдминБит")


async def odata_metadata() -> None:
    async with httpx.AsyncClient(auth=AUTH, timeout=120) as c:
        meta = (await c.get(f"{ODATA}/$metadata")).text
        ents = sorted(set(re.findall(r'EntitySet Name="([^"]+)"', meta)))

        print("=== Registers / Documents (vacation-related keywords) ===")
        keywords = [
            "Остат",
            "Начальн",
            "Фактическ",
            "Планируем",
            "Право",
            "Ежегодн",
            "Отпуск",
            "отпуск",
        ]
        for kw in keywords:
            matched = [e for e in ents if kw in e and ("Register" in e or e.startswith("Document_"))]
            if matched:
                print(f"\n[{kw}] ({len(matched)})")
                for e in matched:
                    print(f"  {e}")

        # probe unpublished-looking registers
        probes = [
            "InformationRegister_ОстаткиОтпусков_RecordType",
            "InformationRegister_ОстаткиОтпусков",
            "InformationRegister_ПланируемыеЕжегодныеОтпуска_RecordType",
            "InformationRegister_ПравоНаОтпуска_RecordType",
            "InformationRegister_ГрафикОтпусков_RecordType",
            "AccumulationRegister_ФактическиеОтпуска/Balance()",
            "AccumulationRegister_ФактическиеОтпуска_RecordType",
            "Document_Отпуск?$top=1&$select=Ref_Key,Number,Date,Сотрудник_Key",
        ]
        print("\n=== HTTP probes (selected) ===")
        for path in probes:
            url = f"{ODATA}/{path}" if not path.startswith("Document") else f"{ODATA}/{path}"
            r = await c.get(url, headers={"Accept": "application/json"})
            print(f"  {path.split('?')[0]}: HTTP {r.status_code}")


async def main() -> None:
    report: dict[str, object] = {}
    await odata_metadata()
    report["conf_doc_search"] = await conf_doc_search_json()
    report["document_movements"] = await conf_doc_otpusk_movements_json()
    out = ROOT / "tests" / "artifacts" / "probe_vacation_deep.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport: {out}")


async def conf_doc_search_json() -> list[dict]:
    queries = [
        "остатки отпусков",
        "Document.Отпуск движения регистры",
        "НачальныеОстаткиОтпусков",
        "ФактическиеОтпуска",
        "ОстаткиОтпусков",
        "ПравоНаОтпуска",
    ]
    all_results: list[dict] = []
    async with httpx.AsyncClient(timeout=120) as c:
        for q in queries:
            body = {"query": q, "configuration": CONF, "top_k": 5}
            r = await c.post(f"{CONF_DOC}/search", json=body)
            hits = r.json() if r.is_success else []
            if not isinstance(hits, list):
                hits = []
            print(f"\n--- search: {q!r} ---")
            row = {"query": q, "hits": []}
            for hit in hits[:5]:
                print(f"  [{hit.get('score', '?')}] {hit.get('object_type')}.{hit.get('name')}")
                row["hits"].append(
                    {
                        "object_type": hit.get("object_type"),
                        "name": hit.get("name"),
                        "score": hit.get("score"),
                        "synonym": hit.get("synonym"),
                    }
                )
            all_results.append(row)
    return all_results


async def conf_doc_otpusk_movements_json() -> dict:
    docs = ["Отпуск", "ОтпускаСотрудников", "ВводНачальныхОстатковОтпусков"]
    result: dict[str, object] = {}
    async with httpx.AsyncClient(timeout=120) as c:
        for doc_name in docs:
            r = await c.get(
                f"{CONF_DOC}/objects/Document/{doc_name}",
                params={"configuration": CONF},
            )
            if not r.is_success:
                result[doc_name] = {"error": r.status_code}
                continue
            obj = r.json()
            chunks_info = obj.get("chunks", [])
            register_lines: list[str] = []
            for ch in chunks_info:
                idx = ch.get("chunk_index", ch.get("index", 0))
                cr = await c.get(
                    f"{CONF_DOC}/objects/Document/{doc_name}/chunks/{idx}",
                    params={"configuration": CONF},
                )
                if not cr.is_success:
                    continue
                text = cr.json().get("text", "")
                for line in text.splitlines():
                    ll = line.lower()
                    if any(
                        w in ll
                        for w in (
                            "регистр",
                            "register",
                            "движен",
                            "остат",
                            "фактическ",
                            "начальн",
                            "право",
                            "планиру",
                            "расчет",
                            "начислен",
                        )
                    ):
                        register_lines.append(line.strip()[:200])
            result[doc_name] = {
                "attributes_count": obj.get("attributes_count"),
                "tabular_sections_count": obj.get("tabular_sections_count"),
                "chunks": len(chunks_info),
                "register_related_lines": register_lines[:40],
            }
            print(f"\n=== Document.{doc_name}: {len(register_lines)} register-related lines ===")
            for line in register_lines[:15]:
                print(f"  {line}")
    return result


async def conf_doc_search() -> None:
    await conf_doc_search_json()


async def conf_doc_otpusk_movements() -> None:
    await conf_doc_otpusk_movements_json()


if __name__ == "__main__":
    asyncio.run(main())
