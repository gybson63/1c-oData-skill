#!/usr/bin/env python3
"""Проверка исправлений: $expand Подразделение и conf-doc search."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.agents.odata.conf_doc_context import build_conf_doc_search_queries, fetch_conf_doc_context  # noqa: E402
from bot.agents.odata.query_builder import build_expand  # noqa: E402
from bot.config import parse_conf_doc_settings  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://localhost/zup_gazaliev/odata/standard.odata"
AUTH = ("АдминБит", "АдминБит")
Q6 = "Покажи 10 штатных сотрудников: ФИО, должность и подразделение"
BRIEF = "Штатные сотрудники: ФИО, должность, подразделение"


async def main() -> None:
    ok = True

    expand = build_expand(
        "Document_НачислениеЗарплаты",
        "Date,Подразделение_Key,Организация_Key",
        ["Date", "Подразделение_Key", "Организация_Key", "Подразделение", "Организация"],
    )
    print(f"build_expand → {expand!r}")
    if expand != "Организация,Подразделение":
        ok = False
        print("FAIL: expected Организация,Подразделение")

    queries = build_conf_doc_search_queries(Q6, BRIEF)
    print(f"conf-doc queries: {queries}")
    if "КадроваяИсторияСотрудников" not in queries or "Сотрудники" not in queries:
        ok = False
        print("FAIL: employee boost queries missing")

    settings = parse_conf_doc_settings(
        {
            "enabled": True,
            "api_url": "http://localhost:8050",
            "configuration": "ЗарплатаИУправлениеПерсоналомКОРП",
            "enrich_prompt": True,
            "search_top_k": 5,
        }
    )
    block = await fetch_conf_doc_context(Q6, settings, request_brief=BRIEF)
    if "Catalog.Сотрудники" not in block:
        ok = False
        print("FAIL: conf-doc block missing Catalog.Сотрудники")
    else:
        print("conf-doc: Catalog.Сотрудники present")

    async with httpx.AsyncClient(auth=AUTH, timeout=60) as client:
        path = (
            "/Document_НачислениеЗарплаты?$top=1"
            "&$expand=Подразделение,Организация&$select=Date,Number,Подразделение_Key"
        )
        r = await client.get(BASE + path, headers={"Accept": "application/json"})
        print(f"OData {path[:60]}... → HTTP {r.status_code}")
        if r.status_code != 200:
            ok = False
            print(r.text[:300])

        hr_path = (
            "/InformationRegister_КадроваяИсторияСотрудников_RecordType?$top=2"
            "&$expand=Сотрудник,Должность,Подразделение"
            "&$select=Сотрудник_Key,Должность_Key,Подразделение_Key"
        )
        r2 = await client.get(BASE + hr_path, headers={"Accept": "application/json"})
        print(f"OData HR expand → HTTP {r2.status_code}")
        if r2.status_code != 200:
            ok = False
            print(r2.text[:300])

    print("\n" + ("ALL OK" if ok else "SOME CHECKS FAILED"))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
