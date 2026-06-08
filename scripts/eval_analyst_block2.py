#!/usr/bin/env python3
"""Прогон аналитика на вопросах блока 2 (zup_gazaliev).

Фиксирует MetadataBrief и вызовы MCP (conf-doc / searxng).
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.agents.analyst.agent_analyst import AnalystAgent  # noqa: E402
from bot.config import build_global_config, load_settings  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.WARNING)

QUESTIONS = [
    {
        "id": 6,
        "q": "Покажи 10 штатных сотрудников: ФИО, должность и подразделение",
        "expect_entity": r"InformationRegister_КадроваяИсторияСотрудников",
    },
    {
        "id": 7,
        "q": "Покажи отпуска сотрудников за май 2025: сотрудник, даты, вид отпуска",
        "expect_entity": r"Document_(Отпуск|ОтпускаСотрудников)",
    },
    {
        "id": 8,
        "q": "Сколько дней отпуска осталось у сотрудников?",
        "expect_entity": r"(InformationRegister_|AccumulationRegister_|Document_).*(Отпуск|отпуск)",
    },
    {
        "id": 9,
        "q": "Покажи начисления зарплаты за апрель 2025",
        "expect_entity": r"(Document_|CalculationRegister_|AccumulationRegister_|InformationRegister_).*(Начисл|Зарплат|Ведомост)",
    },
    {
        "id": 10,
        "q": "Сколько увольнений было в 2024 году?",
        "expect_entity": r"Document_Увольнение",
    },
]


async def main() -> None:
    settings = load_settings("env.json", "default")
    global_cfg = build_global_config(settings)
    agent_cfg = copy.deepcopy(settings.agents_config.get("analyst", {}))

    odata_url = settings.agents_config.get("odata", {}).get("odata_url", "?")
    print("OData base:", odata_url)
    print("AI model:", settings.ai.model)
    searxng_on = (agent_cfg.get("mcp_servers") or {}).get("searxng", {}).get("enabled", False)
    print("SearXNG MCP:", "ON" if searxng_on else "OFF")
    print("=" * 72)

    agent = AnalystAgent()
    await agent.initialize(agent_cfg, global_cfg, cache_dir=".cache", env_file="env.json")
    assert agent.service is not None

    tool_calls: list[str] = []

    async def _track(name: str, args: dict[str, Any]) -> str:
        tool_calls.append(name)
        return await _orig(name, args)

    _orig = agent.service._call_mcp_tool
    agent.service._call_mcp_tool = _track  # type: ignore[method-assign]

    rows: list[dict] = []
    try:
        for item in QUESTIONS:
            tool_calls.clear()
            print(f"\n#{item['id']} {item['q']}")
            print("-" * 72)
            brief = await agent.service.analyze(item["q"])
            entities = [
                o.odata_entity or f"{o.meta_type}.{o.name}" for o in brief.primary_objects + brief.secondary_objects
            ]
            hit = any(re.search(item["expect_entity"], e, re.I) for e in entities)
            used_searxng = any(t.startswith("searxng") or t == "web_url_read" for t in tool_calls)
            used_conf = any(t.startswith("conf_doc") for t in tool_calls)
            conf_idx = next((i for i, t in enumerate(tool_calls) if t.startswith("conf_doc")), None)
            searx_idx = next(
                (i for i, t in enumerate(tool_calls) if t.startswith("searxng") or t == "web_url_read"),
                None,
            )
            order_ok = used_conf and (not used_searxng or (conf_idx is not None and conf_idx < searx_idx))

            print(f"  intent: {brief.intent}")
            print(f"  primary: {[o.odata_entity or o.name for o in brief.primary_objects]}")
            if brief.avoid:
                print(f"  avoid: {brief.avoid}")
            if brief.notes:
                print(f"  notes: {brief.notes[:200]}")
            print(f"  MCP tools: {tool_calls or ['(none — fallback?)']}")
            print(
                f"  conf-doc: {'yes' if used_conf else 'no'} | searxng: {'yes' if used_searxng else 'no'} | order: {'OK' if order_ok else 'BAD'}"
            )
            print(f"  expect hit: {'OK' if hit else 'MISS'}")

            rows.append(
                {
                    "id": item["id"],
                    "question": item["q"],
                    "intent": brief.intent,
                    "primary": [asdict(o) for o in brief.primary_objects],
                    "avoid": brief.avoid,
                    "notes": brief.notes,
                    "mcp_tools": list(tool_calls),
                    "expect_hit": hit,
                    "used_searxng": used_searxng,
                    "used_conf_doc": used_conf,
                    "tool_order_ok": order_ok,
                }
            )
    finally:
        await agent.shutdown()

    print("\n" + "=" * 72)
    print("СВОДКА АНАЛИТИК")
    print("| № | expect | conf-doc | searxng | order | primary entity |")
    print("|---|--------|----------|---------|-------|----------------|")
    hits = order_ok_count = 0
    for r in rows:
        if r["expect_hit"]:
            hits += 1
        if r.get("tool_order_ok", r["used_conf_doc"] or not r["used_searxng"]):
            order_ok_count += 1
        primary = ", ".join(o.get("odata_entity") or o.get("name", "") for o in r["primary"][:2]) or "—"
        conf = "✓" if r.get("used_conf_doc") or any(t.startswith("conf_doc") for t in r["mcp_tools"]) else "·"
        sx = "✓" if r["used_searxng"] else "·"
        exp = "✓" if r["expect_hit"] else "✗"
        ord_mark = "✓" if r.get("tool_order_ok", True) else "✗"
        print(f"| {r['id']} | {exp} | {conf} | {sx} | {ord_mark} | {primary[:40]} |")
    print(f"| **Итого expect** | **{hits}/{len(rows)}** | | | **{order_ok_count}/{len(rows)}** | |")

    out = ROOT / "tests" / "artifacts" / "analyst_block2_results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON: {out}")


if __name__ == "__main__":
    asyncio.run(main())
