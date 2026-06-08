#!/usr/bin/env python3
"""Smoke-test SearXNG MCP connection for analyst."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.mcp_client import MCPClientManager  # noqa: E402
from bot.mcp_config import resolve_mcp_config  # noqa: E402


async def main() -> None:
    env = json.loads((ROOT / "env.json").read_text(encoding="utf-8"))
    profile = env["profiles"]["default"]
    analyst = profile["agents"]["analyst"]
    mcp_config = resolve_mcp_config(profile, analyst)

    manager = MCPClientManager()
    await manager.connect_all(mcp_config)
    try:
        if not manager.is_connected():
            print("FAIL: no MCP servers connected")
            sys.exit(1)

        status = manager.get_status()
        print("MCP status:", json.dumps(status, ensure_ascii=False, indent=2))

        tools = [t["function"]["name"] for t in manager.get_all_openai_tools()]
        if "searxng_web_search" not in tools:
            print("FAIL: searxng_web_search not available, tools=", tools)
            sys.exit(1)

        result = await manager.call_tool(
            "searxng_web_search",
            {"query": "1C OData metadata", "language": "ru"},
        )
        preview = result[:500].replace("\n", " ")
        print("searxng_web_search OK:", preview)
    finally:
        await manager.disconnect_all()


if __name__ == "__main__":
    asyncio.run(main())
