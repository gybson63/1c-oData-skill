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


async def main() -> None:
    async with httpx.AsyncClient(auth=AUTH, timeout=120) as c:
        # Who writes ФактическиеОтпуска / ПериодыОтпусков
        for reg, label in [
            ("AccumulationRegister_ФактическиеОтпуска_RecordType", "ФактическиеОтпуска"),
            ("InformationRegister_ПериодыОтпусков_RecordType", "ПериодыОтпусков"),
        ]:
            r = await c.get(
                f"{BASE}/{reg}?$top=20&$filter=Recorder_Type eq 'StandardODATA.Document_Отпуск'",
                headers={"Accept": "application/json"},
            )
            print(f"=== {label} from Document_Отпуск: HTTP {r.status_code} ===")
            if r.status_code == 200:
                rows = r.json().get("value", [])
                print(f"  count in top filter: {len(rows)}")
                if rows:
                    row = rows[0]
                    print(
                        f"  sample: Количество/Дней={row.get('Количество') or row.get('КоличествоДней')} "
                        f"Сотрудник={row.get('Сотрудник_Key')[:8]}…"
                    )

        # Latest analytics balances
        r = await c.get(
            f"{BASE}/InformationRegister_АналитикаОстатковОтпусков"
            "?$top=10&$orderby=Дата desc&$select=Сотрудник_Key,ВидЕжегодногоОтпуска_Key,Дата,ОстатокДней",
            headers={"Accept": "application/json"},
        )
        print("\n=== АналитикаОстатковОтпусков (latest) ===")
        print(f"HTTP {r.status_code}")
        for row in r.json().get("value", [])[:5]:
            print(f"  {row.get('Дата')[:10]} | ОстатокДней={row.get('ОстатокДней')}")

    async with httpx.AsyncClient(timeout=120) as c:
        card = await c.get(
            f"{CONF}/objects/InformationRegister/АналитикаОстатковОтпусков",
            params={"configuration": CFG},
        )
        print("\n=== conf-doc InformationRegister.АналитикаОстатковОтпусков ===")
        if card.is_success:
            for ch in card.json().get("chunks", []):
                idx = ch.get("chunk_index", 0)
                cr = await c.get(
                    f"{CONF}/objects/InformationRegister/АналитикаОстатковОтпусков/chunks/{idx}",
                    params={"configuration": CFG},
                )
                if cr.is_success:
                    print(cr.json().get("text", "")[:2500])


if __name__ == "__main__":
    asyncio.run(main())
