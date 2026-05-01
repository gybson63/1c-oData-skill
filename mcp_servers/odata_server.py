#!/usr/bin/env python3
"""MCP-сервер для выполнения HTTP-запросов к 1С OData API.

Предоставляет инструмент «fetch» для выполнения GET-запросов
к OData-эндпоинту 1С:Предприятие с Basic-авторизацией.

Переменные окружения:
  ODATA_URL      — базовый URL OData (например http://localhost/zup/odata/standard.odata)
  ODATA_USER     — логин пользователя 1С
  ODATA_PASSWORD — пароль пользователя 1С

Запуск:
  python mcp_servers/odata_server.py
"""

import asyncio
import base64
import json
import logging
import os
import sys
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    TextContent,
    Tool,
)

log = logging.getLogger("1c-odata-mcp")

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------

ODATA_URL = os.environ.get("ODATA_URL", "").rstrip("/")
ODATA_USER = os.environ.get("ODATA_USER", "")
ODATA_PASSWORD = os.environ.get("ODATA_PASSWORD", "")

if not ODATA_URL:
    log.warning("ODATA_URL не задан — запросы не будут работать")

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

app = Server("1c-odata")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """Возвращает список доступных инструментов."""
    return [
        Tool(
            name="fetch",
            description=(
                "Выполняет HTTP-запрос к 1С OData API. "
                "Используется для получения данных из 1С:Предприятие через OData REST API. "
                "Авторизация (Basic) подставляется автоматически из настроек сервера."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Полный URL для запроса (включая параметры $filter, $select и т.д.)",
                    },
                    "method": {
                        "type": "string",
                        "description": "HTTP-метод (GET, POST, PATCH, DELETE)",
                        "enum": ["GET", "POST", "PATCH", "DELETE"],
                        "default": "GET",
                    },
                    "headers": {
                        "type": "object",
                        "description": "Дополнительные HTTP-заголовки (ключ: значение)",
                        "additionalProperties": {"type": "string"},
                    },
                    "body": {
                        "type": "string",
                        "description": "Тело запроса (для POST/PATCH)",
                    },
                },
                "required": ["url"],
            },
        )
    ]


def _error_result(msg: str) -> CallToolResult:
    """Return a CallToolResult with isError=True."""
    return CallToolResult(content=[TextContent(type="text", text=msg)], isError=True)


def _success_result(text: str) -> CallToolResult:
    """Return a CallToolResult with the response body."""
    return CallToolResult(content=[TextContent(type="text", text=text)], isError=False)


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    """Обрабатывает вызов инструмента."""
    if name != "fetch":
        return _error_result(f"Неизвестный инструмент: {name}")

    url = arguments.get("url", "")
    method = arguments.get("method", "GET").upper()
    extra_headers = arguments.get("headers", {})
    body = arguments.get("body")

    if not url:
        return _error_result("Ошибка: не указан URL")

    # Build headers: Basic auth + Accept
    auth_token = base64.b64encode(f"{ODATA_USER}:{ODATA_PASSWORD}".encode("utf-8")).decode("ascii")

    headers = {
        "Authorization": f"Basic {auth_token}",
        "Accept": "application/json",
    }
    headers.update(extra_headers)

    log.info("fetch %s %s", method, url[:200])

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            verify=False,  # 1C self-signed certs
        ) as client:
            if method == "GET":
                resp = await client.get(url, headers=headers)
            elif method == "POST":
                resp = await client.post(url, headers=headers, content=body)
            elif method == "PATCH":
                resp = await client.patch(url, headers=headers, content=body)
            elif method == "DELETE":
                resp = await client.delete(url, headers=headers)
            else:
                return _error_result(f"Неподдерживаемый метод: {method}")

        resp_text = resp.text

        # Error status → isError=True with HTTP status code
        if resp.status_code >= 400:
            error_msg = f"HTTP {resp.status_code}"
            try:
                err_data = json.loads(resp_text)
                odata_err = err_data.get("error", {})
                if isinstance(odata_err, dict):
                    msg_inner = odata_err.get("message", {})
                    if isinstance(msg_inner, dict):
                        error_msg += f": {msg_inner.get('value', resp_text[:500])}"
                    else:
                        error_msg += f": {msg_inner}"
                else:
                    error_msg += f": {odata_err}"
            except (json.JSONDecodeError, KeyError):
                error_msg += f": {resp_text[:500]}"
            return _error_result(error_msg)

        return _success_result(resp_text)

    except httpx.ConnectError as e:
        return _error_result(f"Ошибка соединения: {e}")
    except httpx.TimeoutException as e:
        return _error_result(f"Таймаут запроса: {e}")
    except Exception as e:
        log.exception("fetch error")
        return _error_result(f"Ошибка: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,  # MCP uses stdout for protocol — logs go to stderr
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log.info("1C OData MCP Server starting (url=%s)", ODATA_URL)

    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())