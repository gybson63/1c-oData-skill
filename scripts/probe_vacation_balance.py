#!/usr/bin/env python3
"""Проверка OData-сущностей остатков отпусков на zup_gazaliev."""

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
BASE = "http://localhost/zup_gazaliev/odata/standard.odata"
AUTH = ("АдминБит", "АдминБит")

PROBE_PATHS = [
    "/InformationRegister_АналитикаОстатковОтпусков?$top=2&$select=Сотрудник_Key,ВидЕжегодногоОтпуска_Key,Дата,ОстатокДней&$orderby=Дата desc",
    "/InformationRegister_НачальныеОстаткиОтпусков_RecordType?$top=2",
    "/InformationRegister_НачальныеОстаткиОтпусков?$top=2",
    "/InformationRegister_НачальныеОстаткиОтпусков/SliceLast()?$top=2",
    "/AccumulationRegister_ФактическиеОтпуска/Turnovers()?$top=2",
    "/AccumulationRegister_ФактическиеОтпуска/Balance()?$top=2",
    "/AccumulationRegister_ФактическиеОтпуска_RecordType?$top=2",
    "/InformationRegister_ПоложенныеВидыЕжегодныхОтпусков_RecordType?$top=2",
    "/InformationRegister_ОстаткиОтпусков_RecordType?$top=2",
]

SAMPLE_PATHS = [
    "/InformationRegister_АналитикаОстатковОтпусков?$top=1&$select=Сотрудник_Key,ВидЕжегодногоОтпуска_Key,Дата,ОстатокДней&$orderby=Дата desc",
    "/InformationRegister_НачальныеОстаткиОтпусков_RecordType?$top=1",
    "/AccumulationRegister_ФактическиеОтпуска/Turnovers()?$top=1",
    "/AccumulationRegister_ФактическиеОтпуска_RecordType?$top=1",
]


async def main() -> None:
    async with httpx.AsyncClient(auth=AUTH, timeout=120) as client:
        meta = (await client.get(f"{BASE}/$metadata")).text
        key_regs = sorted(
            {
                e
                for e in re.findall(r'EntitySet Name="([^"]+)"', meta)
                if "Register" in e and any(k in e for k in ("Остат", "Отпуск", "Аналитика"))
            }
        )
        print(f"Registers (Остат/Отпуск/Аналитика) in $metadata ({len(key_regs)}):")
        for e in key_regs[:25]:
            print(f"  {e}")
        if len(key_regs) > 25:
            print(f"  ... +{len(key_regs) - 25} more")

        print("\nHTTP probes:")
        for path in PROBE_PATHS:
            entity = path.split("?")[0].lstrip("/")
            r = await client.get(BASE + path, headers={"Accept": "application/json"})
            snippet = r.text.replace("\n", " ")[:120]
            print(f"  {entity}: HTTP {r.status_code} — {snippet}")

        samples: dict[str, object] = {}
        for path in SAMPLE_PATHS:
            r = await client.get(BASE + path, headers={"Accept": "application/json"})
            key = path.split("?")[0].lstrip("/")
            if r.status_code == 200:
                value = r.json().get("value", [{}])
                samples[key] = value[0] if value else {}
            else:
                samples[key] = {"error": r.status_code, "body": r.text[:500]}

        out = ROOT / "tests" / "artifacts" / "probe_vacation_sample.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nSamples: {out}")


if __name__ == "__main__":
    asyncio.run(main())
