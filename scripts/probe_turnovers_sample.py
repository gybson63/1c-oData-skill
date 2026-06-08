#!/usr/bin/env python3
import asyncio
import sys

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

AUTH = ("АдминБит", "АдминБит")
BASE = "http://localhost/zup_gazaliev/odata/standard.odata"


async def main() -> None:
    async with httpx.AsyncClient(auth=AUTH, timeout=120) as c:
        url = f"{BASE}/AccumulationRegister_ФактическиеОтпуска/Turnovers()?$top=3"
        r = await c.get(url, headers={"Accept": "application/json"})
        print("HTTP", r.status_code)
        import json

        print(json.dumps(r.json(), ensure_ascii=False, indent=2)[:2000])


asyncio.run(main())
