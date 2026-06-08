#!/usr/bin/env python3
"""Тип регистра накопления ФактическиеОтпуска и виртуальные таблицы OData."""

import asyncio
import re
import sys

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

AUTH = ("АдминБит", "АдминБит")
BASE = "http://localhost/zup_gazaliev/odata/standard.odata"
CONF = "http://localhost:8050"
CFG = "ЗарплатаИУправлениеПерсоналомКОРП"
REG = "ФактическиеОтпуска"


async def main() -> None:
    async with httpx.AsyncClient(auth=AUTH, timeout=120) as c:
        meta = (await c.get(f"{BASE}/$metadata")).text
        # FunctionImport for this register
        print("=== FunctionImport in $metadata ===")
        for m in re.finditer(
            rf'FunctionImport Name="([^"]+)"[^>]*EntitySet="AccumulationRegister_{REG}"',
            meta,
        ):
            print(f"  {m.group(1)}")

        # RecordType properties
        rt = f"AccumulationRegister_{REG}_RecordType"
        pos = meta.find(f'EntityType Name="{rt}"')
        if pos >= 0:
            chunk = meta[pos : pos + 2000]
            print(f"\n=== {rt} properties (sample) ===")
            for m in re.finditer(r'Property Name="([^"]+)"', chunk):
                print(f"  {m.group(1)}")

        paths = [
            f"AccumulationRegister_{REG}/Balance()?$top=2",
            f"AccumulationRegister_{REG}/BalanceAndTurnovers()?$top=2",
            f"AccumulationRegister_{REG}/Turnovers()?$top=2",
            f"AccumulationRegister_{REG}_RecordType?$top=2",
        ]
        print("\n=== HTTP probes ===")
        for path in paths:
            r = await c.get(f"{BASE}/{path}", headers={"Accept": "application/json"})
            entity = path.split("?")[0]
            msg = r.text.replace("\n", " ")[:100]
            print(f"  {entity}: HTTP {r.status_code} — {msg}")

    async with httpx.AsyncClient(timeout=120) as c:
        for idx in (0, 1):
            cr = await c.get(
                f"{CONF}/objects/AccumulationRegister/{REG}/chunks/{idx}",
                params={"configuration": CFG},
            )
            if not cr.is_success:
                continue
            text = cr.json().get("text", "")
            print(f"\n=== conf-doc chunk {idx} (register kind hints) ===")
            for line in text.splitlines():
                ll = line.lower()
                if any(
                    w in ll
                    for w in (
                        "остат",
                        "оборот",
                        "registerkind",
                        "вид регистра",
                        "balance",
                        "turnover",
                        "resources",
                        "ресурс",
                        "измерен",
                    )
                ):
                    print(f"  {line}")


if __name__ == "__main__":
    asyncio.run(main())
