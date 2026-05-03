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
from typing import Any

log = logging.getLogger("1c-bot.mcp")


class MCPToolError(Exception):
    """Raised when an MCP tool returns an error response (isError=True)."""
    def __init__(self, message: str, details: str = ""):
        super().__init__(message)
        self.details = details


# ---------------------------------------------------------------------------
# Lazy import — mcp package may not be installed
# ---------------------------------------------------------------------------

_mcp_available: bool | None = None


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
# MCP Server Connection (background-task pattern to avoid anyio scope issues)
# ---------------------------------------------------------------------------

class MCPServerConnection:
    """Manages a connection to a single MCP server.

    The entire connection lifecycle (stdio_client / sse_client context managers)
    runs inside a dedicated background asyncio Task.  This avoids anyio's
    «cancel scope entered / exited in different tasks» error because both
    ``__aenter__`` and ``__aexit__`` happen in the *same* task.
    """

    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config
        self.transport_type = config.get("transport", "stdio")
        self.session: Any = None
        self._tools_cache: list[Any] = []
        self._openai_tools: list[dict] = []

        # Background task that owns the connection context managers
        self._bg_task: asyncio.Task | None = None
        self._ready: asyncio.Event = asyncio.Event()
        self._connected: bool = False
        self._connect_error: str | None = None

    # -- public API ----------------------------------------------------------

    async def connect(self) -> bool:
        """Spawn the background task and wait until it reports ready."""
        if not _check_mcp_available():
            return False

        self._ready = asyncio.Event()
        self._bg_task = asyncio.create_task(self._bg_run(), name=f"mcp-{self.name}")
        await self._ready.wait()

        if self._connect_error:
            log.error("MCP [%s]: %s", self.name, self._connect_error)
        return self._connected

    async def disconnect(self) -> None:
        """Cancel the background task (which triggers proper cleanup)."""
        if self._bg_task and not self._bg_task.done():
            self._bg_task.cancel()
            try:
                await self._bg_task
            except asyncio.CancelledError:
                pass
        self._bg_task = None
        self.session = None
        self._connected = False
        log.info("MCP [%s]: отключён", self.name)

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on this server.  Raises MCPToolError on failure."""
        if not self._connected or not self.session:
            raise MCPToolError(f"MCP-сервер '{self.name}' не подключён")

        log.info("MCP [%s]: вызов инструмента %s(%s)", self.name, tool_name,
                  json.dumps(arguments, ensure_ascii=False)[:200])

        result = await self.session.call_tool(tool_name, arguments)

        parts = []
        for content in result.content:
            if hasattr(content, "text"):
                parts.append(content.text)
            elif hasattr(content, "data"):
                parts.append(content.data)
            else:
                parts.append(str(content))

        text = "\n".join(parts) if parts else ""

        if getattr(result, "isError", False):
            raise MCPToolError(f"MCP tool '{tool_name}' вернул ошибку", details=text)

        return text

    def get_openai_tools(self) -> list[dict]:
        return self._openai_tools

    def has_tool(self, name: str) -> bool:
        return any(t.name == name for t in self._tools_cache)

    # -- background task -----------------------------------------------------

    async def _bg_run(self) -> None:
        """Owns the MCP context managers.  Runs until cancelled."""
        try:
            if self.transport_type == "stdio":
                await self._bg_stdio()
            elif self.transport_type == "sse":
                await self._bg_sse()
            else:
                self._connect_error = f"неизвестный транспорт '{self.transport_type}'"
                self._ready.set()
        except asyncio.CancelledError:
            log.debug("MCP [%s]: background task cancelled", self.name)
        except Exception as e:
            self._connect_error = str(e)
            self._ready.set()

    async def _bg_stdio(self) -> None:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        command = self.config.get("command", "")
        if not command:
            self._connect_error = "не указан 'command' для stdio транспорта"
            self._ready.set()
            return

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

        # All context managers live & die inside this single task
        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                self.session = session
                await self._load_tools()
                self._connected = True
                self._ready.set()
                log.info("MCP [%s]: подключён (stdio: %s %s)", self.name, command, " ".join(args))

                # Park until cancelled — keeps the context managers alive
                await asyncio.Future()

    async def _bg_sse(self) -> None:
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        url = self.config.get("url", "")
        if not url:
            self._connect_error = "не указан 'url' для SSE транспорта"
            self._ready.set()
            return

        headers = self.config.get("headers", {})

        async with sse_client(url=url, headers=headers) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                self.session = session
                await self._load_tools()
                self._connected = True
                self._ready.set()
                log.info("MCP [%s]: подключён (sse: %s)", self.name, url)

                await asyncio.Future()

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
        """Connect to all configured MCP servers."""
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
        except MCPToolError:
            raise
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
