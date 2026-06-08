#!/usr/bin/env python3
"""Разрешение MCP-конфигурации: общие серверы профиля + per-agent."""

from __future__ import annotations

from typing import Any


def resolve_mcp_config(
    profile_config: dict[str, Any],
    agent_config: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Собрать MCP-конфиг агента из shared + agent-specific секций.

    ``profiles.<p>.mcp_servers`` — общие серверы.
    ``agents.<name>.mcp_servers`` — специфичные; перекрывают shared по имени.
    ``mcp_inherit`` (default True) — merge; False — только agent-секция.
    ``enabled: false`` — сервер пропускается.
    """
    shared = profile_config.get("mcp_servers") or {}
    agent = agent_config.get("mcp_servers") or {}
    inherit = agent_config.get("mcp_inherit", True)
    merged: dict[str, dict[str, Any]] = {**shared, **agent} if inherit else dict(agent)
    return {name: cfg for name, cfg in merged.items() if isinstance(cfg, dict) and cfg.get("enabled", True)}
