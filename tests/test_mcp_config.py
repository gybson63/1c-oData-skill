#!/usr/bin/env python3
"""Тесты resolve_mcp_config."""

from bot.mcp_config import resolve_mcp_config


def test_merge_shared_and_agent():
    profile = {
        "mcp_servers": {
            "conf-doc": {"command": "python", "args": ["conf"]},
            "shared-only": {"command": "node", "args": ["x"]},
        }
    }
    agent = {
        "mcp_inherit": True,
        "mcp_servers": {
            "odata": {"command": "python", "args": ["odata"]},
            "conf-doc": {"command": "python", "args": ["override"]},
        },
    }
    result = resolve_mcp_config(profile, agent)
    assert "conf-doc" in result
    assert result["conf-doc"]["args"] == ["override"]
    assert "odata" in result
    assert "shared-only" in result


def test_agent_override_shared_disabled():
    profile = {"mcp_servers": {"conf-doc": {"command": "a"}}}
    agent = {
        "mcp_inherit": True,
        "mcp_servers": {"searxng": {"enabled": False, "command": "npx"}},
    }
    result = resolve_mcp_config(profile, agent)
    assert "conf-doc" in result
    assert "searxng" not in result


def test_no_inherit():
    profile = {"mcp_servers": {"conf-doc": {"command": "a"}}}
    agent = {
        "mcp_inherit": False,
        "mcp_servers": {"odata": {"command": "b"}},
    }
    result = resolve_mcp_config(profile, agent)
    assert "conf-doc" not in result
    assert "odata" in result


def test_empty_configs():
    assert resolve_mcp_config({}, {}) == {}
