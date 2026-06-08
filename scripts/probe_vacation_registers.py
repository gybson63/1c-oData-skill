#!/usr/bin/env python3
import asyncio
import sys

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

AUTH = ("АдминБит", "АдминБит")
BASE = "http://localhost/zup_gazaliev/odata/standard.odata"
CONF = "http://localhost:8050"
CFG = "ЗарплатаИУправлениеПерсоналомКОРП"

PATHS = [
    "InformationRegister_АналитикаОстатковОтпусков?$top=5&$orderby=Дата desc&$select=Сотрудник_Key,ВидЕжегодногоОтпуска_Key,Дата,ОстатокДней",
    "InformationRegister_АналитикаОстатковОтпусков/SliceLast()?$top=5",
    "InformationRegister_АналитикаОстатковОтпусков_RecordType?$top=2",
    "InformationRegister_ПлановыеЕжегодныеОтпуска_RecordType?$top=2",
    "InformationRegister_ПоложенныеВидыЕжегодныхОтпусков_RecordType?$top=2",
    "InformationRegister_ПериодыОтпусков_RecordType?$top=2",
    "InformationRegister_РасчетРезерваОтпусков_RecordType?$top=2",
    "Document_ВводНачальныхОстатковОтпусков?$top=1",
    "Document_Отпуск?$top=1&$select=Ref_Key,Number,Date,Сотрудник_Key",
]


async def odata() -> None:
    async with httpx.AsyncClient(auth=AUTH, timeout=120) as c:
        print("=== OData probes ===")
        for path in PATHS:
            r = await c.get(f"{BASE}/{path}", headers={"Accept": "application/json"})
            entity = path.split("?")[0]
            print(f"{entity}: HTTP {r.status_code}")
            if r.status_code == 200:
                row = (r.json().get("value") or [{}])[0]
                keys = [k for k in row if not k.endswith("@navigationLinkUrl")]
                print(f"  fields: {keys[:15]}")
                if "ОстатокДней" in row:
                    print(f"  sample ОстатокДней={row.get('ОстатокДней')} Дата={row.get('Дата')}")


async def conf_doc_registers() -> None:
    searches = [
        ("остатки отпусков регистр", None),
        ("ФактическиеОтпуска", "AccumulationRegister"),
        ("ПоложенныеВидыЕжегодныхОтпусков", "InformationRegister"),
        ("ПериодыОтпусков", "InformationRegister"),
        ("АналитикаОстатковОтпусков", "InformationRegister"),
        ("движения документ отпуск", None),
    ]
    async with httpx.AsyncClient(timeout=120) as c:
        print("\n=== conf-doc searches ===")
        for q, ot in searches:
            body = {"query": q, "configuration": CFG, "top_k": 8}
            if ot:
                body["object_type"] = ot
            hits = (await c.post(f"{CONF}/search", json=body)).json()
            print(f"\n[{q}]")
            for h in hits[:8]:
                print(f"  {h.get('object_type')}.{h.get('name')} ({h.get('score', 0):.3f})")

        # Report + CommonModule chunks
        for ot, name in [
            ("Report", "ОстаткиОтпусков"),
            ("CommonModule", "ОстаткиОтпусков"),
            ("AccumulationRegister", "ФактическиеОтпуска"),
            ("InformationRegister", "ПоложенныеВидыЕжегодныхОтпусков"),
        ]:
            card = await c.get(
                f"{CONF}/objects/{ot}/{name}",
                params={"configuration": CFG},
            )
            if not card.is_success:
                print(f"\n{ot}.{name}: HTTP {card.status_code}")
                continue
            obj = card.json()
            print(f"\n=== {ot}.{name} chunks={len(obj.get('chunks', []))} ===")
            for ch in obj.get("chunks", [])[:6]:
                idx = ch.get("chunk_index", 0)
                cr = await c.get(
                    f"{CONF}/objects/{ot}/{name}/chunks/{idx}",
                    params={"configuration": CFG},
                )
                if not cr.is_success:
                    continue
                text = cr.json().get("text", "")
                for line in text.splitlines()[:25]:
                    if line.strip():
                        print(f"  {line[:160]}")


async def main() -> None:
    await odata()
    await conf_doc_registers()


if __name__ == "__main__":
    asyncio.run(main())
