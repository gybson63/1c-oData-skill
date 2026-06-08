#!/usr/bin/env python3
"""Диагностика только вопроса №8 блока 2 (остатки отпусков)."""

from __future__ import annotations

import asyncio
import copy
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.agents.odata.agent_1c_odata import ODataAgent  # noqa: E402
from bot.config import build_global_config, load_settings  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.WARNING)

QUESTION = "Сколько дней отпуска осталось у сотрудников?"


async def run(conf_doc_on: bool) -> None:
    settings = load_settings("env.json", "default")
    global_cfg = build_global_config(settings)
    agent_cfg = copy.deepcopy(settings.agents_config.get("odata", {}))
    if not conf_doc_on:
        agent_cfg["conf_doc"] = {"enabled": False, "enrich_prompt": False}

    agent = ODataAgent()
    await agent.initialize(agent_cfg, global_cfg, cache_dir=".cache", env_file="env.json")
    assert agent._pipeline is not None

    print("\n" + "=" * 72)
    print("conf_doc:", "ON" if conf_doc_on else "OFF")
    print("=" * 72)

    try:
        state = await agent._pipeline.run(QUESTION, [], chat_id=None)
    except Exception as exc:
        print("EXCEPTION:", exc)
        await agent.shutdown()
        return

    q = state.query
    if q:
        print("entity:", q.entity)
        print("select:", q.select)
        print("expand:", q.expand)
        print("filter:", q.filter_expr)
        print("count:", q.count)
    print("records:", len(state.records), "total:", state.total)
    preview = re.sub(r"<[^>]+>", " ", state.answer_html or "")
    print("\nanswer:", preview[:600])
    await agent.shutdown()


async def main() -> None:
    await run(conf_doc_on=True)
    await run(conf_doc_on=False)


if __name__ == "__main__":
    asyncio.run(main())
