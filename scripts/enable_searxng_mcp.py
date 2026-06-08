#!/usr/bin/env python3
"""Patch env.json: enable SearXNG MCP for analyst agent."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / "env.json"

SEARXNG_MCP = {
    "enabled": True,
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "mcp-searxng"],
    "env": {
        "SEARXNG_URL": "http://127.0.0.1:8080",
    },
}

ALLOWED_MCP_TOOLS = [
    "conf_doc_search",
    "conf_doc_get_object",
    "conf_doc_get_object_chunk",
    "conf_doc_list_objects",
    "conf_doc_list_configurations",
    "conf_doc_health",
    "searxng_web_search",
    "web_url_read",
]


def main() -> None:
    data = json.loads(ENV_FILE.read_text(encoding="utf-8"))
    analyst = data["profiles"]["default"]["agents"]["analyst"]
    mcp_servers = analyst.setdefault("mcp_servers", {})
    mcp_servers["searxng"] = SEARXNG_MCP
    analyst["allowed_mcp_tools"] = ALLOWED_MCP_TOOLS
    ENV_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("env.json updated: analyst.searxng enabled")


if __name__ == "__main__":
    main()
