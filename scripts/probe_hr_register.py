#!/usr/bin/env python3
"""Find OData entity sets for HR register."""

import asyncio
import re

import httpx

REG = "InformationRegister_КадроваяИсторияСотрудников"
BASE = "http://localhost/zup_gazaliev/odata/standard.odata"
AUTH = ("АдминБит", "АдминБит")

PATHS = [
    f"/{REG}_RecordType?$top=1",
    f"/{REG}_RecordType?$select=Сотрудник_Key,Должность_Key,Подразделение_Key&$top=3",
    f"/{REG}_RecordType?$orderby=Period desc&$top=10",
    f"/{REG}/SliceLast()?$top=3",
]


async def main() -> None:
    async with httpx.AsyncClient(auth=AUTH, timeout=120) as client:
        meta = (await client.get(f"{BASE}/$metadata")).text
        for m in re.finditer(rf'<EntitySet[^>]*Name="([^"]*{re.escape(REG)}[^"]*)"', meta):
            print("EntitySet:", m.group(1))
        for m in re.finditer(
            rf'<FunctionImport[^>]*Name="([^"]+)"[^>]*EntitySet="([^"]*{re.escape(REG)}[^"]*)"',
            meta,
        ):
            print("FunctionImport:", m.group(1), "->", m.group(2))

        for path in PATHS:
            r = await client.get(BASE + path, headers={"Accept": "application/json"})
            print(f"\n{path}\nHTTP {r.status_code}\n{r.text[:400]}")


if __name__ == "__main__":
    asyncio.run(main())
