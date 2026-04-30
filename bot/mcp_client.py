#!/usr/bin/env python3
"""MCP-клиент для подключения внешних MCP-серверов к боту.

Загружает конфигурацию MCP-серверов из env.json (поле «mcp_servers»),
подключается к ним при старте бота и предоставляет список инструментов
для передачи ИИ через протокол OpenAI function calling.

Поддерживаемые типы транспорта:
  - stdio  — запуск MCP-сервера как дочернего процесса
  - sse    — подключение к HTTP SSE-эндпоинту

Пример конфигурации в env.json:

{
  "default": {
    ...,
    "mcp_servers": {
      "my_server": {
        "transport": "stdio",
        "command": "python",
        "args": ["path/to/server.py"],
        "env": {"API_KEY": "..."}
      },
      "remote": {
        "transport": "sse",
        "url": "http://localhost:8080/sse"
      }
    }
  }
}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("1c-bot.mcp")

# ---------------------------------------------------------------------------
# Lazy import — mcp package may not be installed
# ---------------------------------------------------------------------------

_mcp_available: Optional[bool] = None


def _check_mcp_available() -> bool:
    global _mcp_available
    if _mcp_available is None:
        try:
            from mcp import ClientSession  # noqa: F401
            _mcp_available = True
        except ImportError:
            _mcp_available = False
            log.warning(
                "Пакет 'mcp' не установлен. MCP-серверы не будут подключены. "
                "Установите: pip install mcp"
            )
    return _mcp_available


# ---------------------------------------------------------------------------
# MCP tool → OpenAI function calling format converter
# ---------------------------------------------------------------------------

def _mcp_tool_to_openai(tool: Any) -> dict:
    """Convert an MCP Tool object to OpenAI function calling format."""
    schema = tool.inputSchema or {"type": "object", "properties": {}}
    # Ensure schema has required structure
    if "type" not in schema:
        schema["type"] = "object"
    if "properties" not in schema:
        schema["properties"] = {}

    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": schema,
        },
    }


# ---------------------------------------------------------------------------
# MCP Server Connection
# ---------------------------------------------------------------------------

class MCPServerConnection:
    """Manages a connection to a single MCP server."""

    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config
        self.transport_type = config.get("transport", "stdio")
        self.session: Any = None
        self._read_stream: Any = None
        self._write_stream: Any = None
        self._process: Any = None
        self._cleanup_cm: Any = None
        self._tools_cache: list[Any] = []
        self._openai_tools: list[dict] = []

    async def connect(self) -> bool:
        """Connect to the MCP server. Returns True on success."""
        if not _check_mcp_available():
            return False

        try:
            if self.transport_type == "stdio":
                return await self._connect_stdio()
            elif self.transport_type == "sse":
                return await self._connect_sse()
            else:
                log.error("MCP [%s]: неизвестный транспорт '%s'", self.name, self.transport_type)
                return False
        except Exception as e:
            log.error("MCP [%s]: ошибка подключения: %s", self.name, e)
            return False

    async def _connect_stdio(self) -> bool:
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client, StdioServerParameters

        command = self.config.get("command", "")
        if not command:
            log.error("MCP [%s]: не указан 'command' для stdio транспорта", self.name)
            return False

        # Resolve command path
        resolved = shutil.which(command)
        if resolved:
            command = resolved

        args = self.config.get("args", [])
        env_extra = self.config.get("env", {})
        cwd = self.config.get("cwd")

        env = os.environ.copy()
        env.update(env_extra)

        server_params = StdioServerParameters(
            command=command,
            args=args,
            env=env if env_extra else None,
            cwd=cwd,
        )

        # stdio_client returns an async context manager
        self._cleanup_cm = stdio_client(server_params)
        streams = await self._cleanup_cm.__aenter__()
        self._read_stream, self._write_stream = streams

        self.session = ClientSession(self._read_stream, self._write_stream)
        await self.session.__aenter__()

        # Initialize
        await self.session.initialize()
        log.info("MCP [%s]: подключён (stdio: %s %s)", self.name, command, " ".join(args))

        # Load tools
        await self._load_tools()
        return True

    async def _connect_sse(self) -> bool:
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        url = self.config.get("url", "")
        if not url:
            log.error("MCP [%s]: не указан 'url' для SSE транспорта", self.name)
            return False

        headers = self.config.get("headers", {})

        self._cleanup_cm = sse_client(url=url, headers=headers)
        streams = await self._cleanup_cm.__aenter__()
        self._read_stream, self._write_stream = streams

        self.session = ClientSession(self._read_stream, self._write_stream)
        await self.session.__aenter__()

        await self.session.initialize()
        log.info("MCP [%s]: подключён (sse: %s)", self.name, url)

        await self._load_tools()
        return True

    async def _load_tools(self) -> None:
        result = await self.session.list_tools()
        self._tools_cache = result.tools
        self._openai_tools = [_mcp_tool_to_openai(t) for t in self._tools_cache]
        log.info(
            "MCP [%s]: загружено %d инструментов: %s",
            self.name,
            len(self._tools_cache),
            ", ".join(t.name for t in self._tools_cache),
        )

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on this server and return the result as text."""
        if not self.session:
            return f"Ошибка: MCP-сервер '{self.name}' не подключён"

        log.info("MCP [%s]: вызов инструмента %s(%s)", self.name, tool_name,
                  json.dumps(arguments, ensure_ascii=False)[:200])

        result = await self.session.call_tool(tool_name, arguments)

        # Collect all text content
        parts = []
        for content in result.content:
            if hasattr(content, "text"):
                parts.append(content.text)
            elif hasattr(content, "data"):
                parts.append(content.data)
            else:
                parts.append(str(content))

        return "\n".join(parts) if parts else ""

    def get_openai_tools(self) -> list[dict]:
        """Return tools in OpenAI function calling format."""
        return self._openai_tools

    def has_tool(self, name: str) -> bool:
        """Check if this server has a tool with the given name."""
        return any(t.name == name for t in self._tools_cache)

    async def disconnect(self) -> None:
        """Disconnect from the MCP server."""
        try:
            if self.session:
                await self.session.__aexit__(None, None, None)
                self.session = None
            if self._cleanup_cm:
                await self._cleanup_cm.__aexit__(None, None, None)
                self._cleanup_cm = None
            log.info("MCP [%s]: отключён", self.name)
        except Exception as e:
            log.warning("MCP [%s]: ошибка при отключении: %s", self.name, e)


# ---------------------------------------------------------------------------
# MCP Client Manager
# ---------------------------------------------------------------------------

class MCPClientManager:
    """Manages connections to multiple MCP servers.

    Reads configuration from env.json, connects to all configured servers,
    and provides unified access to their tools for the AI agent.
    """

    def __init__(self):
        self._servers: dict[str, MCPServerConnection] = {}
        self._tool_to_server: dict[str, str] = {}  # tool_name → server_name

    async def connect_all(self, mcp_config: dict[str, dict]) -> None:
        """Connect to all configured MCP servers.

        Args:
            mcp_config: Map of server_name → server_config from env.json.
        """
        if not mcp_config:
            log.info("MCP: серверы не настроены")
            return

        if not _check_mcp_available():
            return

        log.info("MCP: подключение к %d серверам...", len(mcp_config))

        for name, config in mcp_config.items():
            conn = MCPServerConnection(name, config)
            success = await conn.connect()
            if success:
                self._servers[name] = conn
                # Register tools in the lookup
                for t in conn._tools_cache:
                    if t.name in self._tool_to_server:
                        existing = self._tool_to_server[t.name]
                        log.warning(
                            "MCP: инструмент '%s' дублируется между '%s' и '%s'. "
                            "Будет использован '%s'",
                            t.name, existing, name, existing,
                        )
                    else:
                        self._tool_to_server[t.name] = name

        total_tools = sum(len(s._tools_cache) for s in self._servers.values())
        log.info(
            "MCP: подключено %d/%d серверов, %d инструментов",
            len(self._servers), len(mcp_config), total_tools,
        )

    async def disconnect_all(self) -> None:
        """Disconnect from all MCP servers."""
        for conn in self._servers.values():
            await conn.disconnect()
        self._servers.clear()
        self._tool_to_server.clear()

    def get_all_openai_tools(self) -> list[dict]:
        """Get all tools from all connected servers in OpenAI format."""
        tools = []
        seen = set()
        for conn in self._servers.values():
            for t in conn.get_openai_tools():
                fname = t["function"]["name"]
                if fname not in seen:
                    tools.append(t)
                    seen.add(fname)
        return tools

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool by name on the appropriate server."""
        server_name = self._tool_to_server.get(tool_name)
        if not server_name:
            return f"Неизвестный инструмент: {tool_name}"

        conn = self._servers.get(server_name)
        if not conn:
            return f"MCP-сервер '{server_name}' не подключён"

        try:
            return await conn.call_tool(tool_name, arguments)
        except Exception as e:
            log.error("MCP [%s]: ошибка вызова %s: %s", server_name, tool_name, e)
            return f"Ошибка MCP [{server_name}] {tool_name}: {e}"

    def is_connected(self) -> bool:
        return len(self._servers) > 0

    def get_status(self) -> dict[str, Any]:
        """Return status info about connected servers."""
        result = {}
        for name, conn in self._servers.items():
            result[name] = {
                "transport": conn.transport_type,
                "tools": [t.name for t in conn._tools_cache],
            }
        return result

    async def reconnect(self, mcp_config: dict[str, dict]) -> None:
        """Disconnect all and reconnect."""
        await self.disconnect_all()
        await self.connect_all(mcp_config)