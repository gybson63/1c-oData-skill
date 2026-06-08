#!/usr/bin/env python3
"""Проверка полей Document_НачислениеЗарплаты и Подразделение* в OData."""

from __future__ import annotations

import asyncio
import re

import httpx

BASE = "http://localhost/zup_gazaliev/odata/standard.odata"
AUTH = ("АдминБит", "АдминБит")


def extract_entity_type(xml: str, entity: str) -> tuple[list[str], list[str]]:
    m = re.search(
        rf'<EntityType Name="{re.escape(entity)}"[^>]*>(.*?)</EntityType>',
        xml,
        re.S,
    )
    if not m:
        return [], []
    block = m.group(1)
    props = re.findall(r'<Property Name="([^"]+)"', block)
    navs = re.findall(r'<NavigationProperty Name="([^"]+)"', block)
    return props, navs


async def main() -> None:
    async with httpx.AsyncClient(auth=AUTH, timeout=120) as client:
        meta = (await client.get(f"{BASE}/$metadata")).text

        for entity in (
            "Document_НачислениеЗарплаты",
            "InformationRegister_КадроваяИсторияСотрудников_RowType",
            "Catalog_Сотрудники",
        ):
            props, navs = extract_entity_type(xml=meta, entity=entity)
            print(f"\n=== {entity} ===")
            print(
                "props (подраздел/организ/дата):",
                [p for p in props if any(x in p for x in ("Подраздел", "Организ", "Date", "Дата", "Месяц", "Period"))],
            )
            print(
                "nav (подраздел/организ/сотруд):",
                [n for n in navs if any(x in n for x in ("Подраздел", "Организ", "Сотруд", "Должн"))],
            )

        paths = [
            "/Document_НачислениеЗарплаты?$top=1&$select=Date,Number,Организация_Key",
            "/Document_НачислениеЗарплаты?$top=1&$select=Date,Number,ПодразделениеОрганизации_Key",
            "/Document_НачислениеЗарплаты?$top=1&$expand=ПодразделениеОрганизации&$select=Date,Number",
            "/Document_НачислениеЗарплаты?$top=1&$expand=Организация&$select=Date,Number",
            "/Document_НачислениеЗарплаты?$top=1&$filter=year(Date) eq 2025 and month(Date) eq 4&$select=Date,Number",
            "/InformationRegister_КадроваяИсторияСотрудников_RecordType?$top=2"
            "&$select=Сотрудник_Key,Должность_Key,Подразделение_Key",
            "/InformationRegister_КадроваяИсторияСотрудников_RecordType?$top=2"
            "&$select=Сотрудник_Key,Должность_Key,ПодразделениеОрганизации_Key",
            "/InformationRegister_КадроваяИсторияСотрудников_RecordType?$top=2"
            "&$expand=Сотрудник,Должность,ПодразделениеОрганизации"
            "&$select=Сотрудник_Key,Должность_Key,Подразделение_Key",
            "/InformationRegister_КадроваяИсторияСотрудников_RecordType?$top=2"
            "&$expand=Сотрудник,Должность,Подразделение&$select=Сотрудник_Key,Должность_Key,Подразделение_Key",
        ]
        print("\n=== HTTP probes ===")
        for path in paths:
            r = await client.get(BASE + path, headers={"Accept": "application/json"})
            body = r.text.replace("\n", " ")[:280]
            print(f"\n{path}\nHTTP {r.status_code}: {body}")


if __name__ == "__main__":
    asyncio.run(main())
