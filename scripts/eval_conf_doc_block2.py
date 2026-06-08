#!/usr/bin/env python3
"""Прогон блока 2 чеклиста: OData-запросы с/без conf-doc.

При включённом agents.analyst с preprocessor_for_odata в Step 1 также
попадает блок «АНАЛИЗ МЕТАДАННЫХ (analyst)».
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.agents.odata.agent_1c_odata import ODataAgent  # noqa: E402
from bot.config import build_global_config, load_settings  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.WARNING)

QUESTIONS = [
    {
        "id": 6,
        "q": "Покажи 10 штатных сотрудников: ФИО, должность и подразделение",
        "expect_entity": r"InformationRegister_КадроваяИсторияСотрудников",
        "expect_kind": "list",
    },
    {
        "id": 7,
        "q": "Покажи отпуска сотрудников за май 2025: сотрудник, даты, вид отпуска",
        "expect_entity": r"Document_(Отпуск|ОтпускаСотрудников)",
        "expect_kind": "list",
    },
    {
        "id": 8,
        "q": "Сколько дней отпуска осталось у сотрудников?",
        "expect_entity": r"(InformationRegister_|AccumulationRegister_|Document_).*(Отпуск|отпуск)",
        "expect_kind": "data",
    },
    {
        "id": 9,
        "q": "Покажи начисления зарплаты за апрель 2025",
        "expect_entity": r"(Document_|CalculationRegister_|AccumulationRegister_|InformationRegister_).*(Начисл|Зарплат|Ведомост)",
        "expect_kind": "list",
    },
    {
        "id": 10,
        "q": "Сколько увольнений было в 2024 году?",
        "expect_entity": r"Document_Увольнение",
        "expect_kind": "count",
    },
]


@dataclass
class RunResult:
    entity: str
    filter_expr: str | None
    records: int
    total: int
    count_mode: bool
    conf_doc_len: int
    error: bool
    answer_preview: str


def score_result(item: dict, run: RunResult) -> int:
    if run.error:
        return -1
    entity = run.entity or ""
    if not entity:
        return -1
    if not re.search(item["expect_entity"], entity, re.I):
        return 0
    if item["expect_kind"] == "count" and run.count_mode:
        return 2
    if run.records > 0 or run.total > 0 or run.count_mode:
        return 2
    # правильный entity, но пустые данные
    if "❌" not in run.answer_preview and entity:
        return 1
    return 0


async def run_one(agent_cfg: dict, global_cfg: dict, question: str, conf_doc_on: bool) -> RunResult:
    cfg = copy.deepcopy(agent_cfg)
    if not conf_doc_on:
        cfg["conf_doc"] = {"enabled": False, "enrich_prompt": False}

    agent = ODataAgent()
    await agent.initialize(cfg, global_cfg, cache_dir=".cache", env_file="env.json")
    assert agent._pipeline is not None

    try:
        state = await agent._pipeline.run(question, [], chat_id=None)
    except Exception as exc:
        conf_len = len(agent._pipeline._conf_doc_block)
        await agent.shutdown()
        return RunResult("", None, 0, 0, False, conf_len, True, str(exc)[:200])

    conf_len = len(agent._pipeline._conf_doc_block)

    q = state.query
    entity = q.entity if q else ""
    filt = q.filter_expr if q else None
    count_mode = bool(q and q.count)
    preview = re.sub(r"<[^>]+>", " ", state.answer_html or "")[:300]
    error = "❌" in (state.answer_html or "") or bool(state.error)

    await agent.shutdown()
    return RunResult(
        entity=entity,
        filter_expr=filt,
        records=len(state.records),
        total=state.total,
        count_mode=count_mode,
        conf_doc_len=conf_len,
        error=error,
        answer_preview=preview.strip(),
    )


async def main() -> None:
    settings = load_settings("env.json", "default")
    global_cfg = build_global_config(settings)
    agent_cfg = copy.deepcopy(settings.agents_config.get("odata", {}))

    print("OData:", agent_cfg.get("odata_url", "?"))
    print("AI model:", settings.ai.model)
    print("=" * 72)

    rows: list[dict] = []
    for item in QUESTIONS:
        print(f"\n#{item['id']} {item['q']}")
        print("-" * 72)
        results: dict[str, RunResult] = {}
        scores: dict[str, int] = {}
        for label, on in (("with", True), ("without", False)):
            print(f"  Запуск ({label} conf-doc)...")
            results[label] = await run_one(agent_cfg, global_cfg, item["q"], on)
            r = results[label]
            scores[label] = score_result(item, r)
            print(f"  [{label}] conf_doc_block={r.conf_doc_len} entity={r.entity!r}")
            if r.filter_expr:
                print(f"        filter={r.filter_expr[:120]}")
            print(f"        records={r.records} total={r.total} count={r.count_mode} score={scores[label]}")
            if r.answer_preview:
                print(f"        answer: {r.answer_preview[:180]}...")

        delta = scores["with"] - scores["without"]
        rows.append(
            {
                "id": item["id"],
                "with": scores["with"],
                "without": scores["without"],
                "delta": delta,
                "entity_with": results["with"].entity,
                "entity_without": results["without"].entity,
            }
        )
        print(f"  Δ = {delta:+d}")

    print("\n" + "=" * 72)
    print("СВОДКА БЛОК 2")
    print("| № | С conf-doc | Без | Δ | entity (с / без) |")
    print("|---|------------|-----|---|------------------|")
    tw = tw0 = 0
    for r in rows:
        tw += r["with"]
        tw0 += r["without"]
        ent = f"{r['entity_with'] or '—'} / {r['entity_without'] or '—'}"
        print(f"| {r['id']} | {r['with']} | {r['without']} | {r['delta']:+d} | {ent[:50]} |")
    print(f"| **Итого** | **{tw}** | **{tw0}** | **{tw - tw0:+d}** | |")

    out = ROOT / "tests" / "artifacts" / "conf_doc_block2_results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON: {out}")


if __name__ == "__main__":
    asyncio.run(main())
