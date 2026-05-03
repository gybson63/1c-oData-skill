#!/usr/bin/env python3
"""MCP-сервер для выполнения HTTP-запросов к 1С OData API.

Предоставляет инструмент «fetch» для выполнения GET-запросов
к OData-эндпоинту 1С:Предприятие с Basic-авторизацией.

Переменные окружения:
  ODATA_URL            — базовый URL OData (например http://localhost/zup/odata/standard.odata)
  ODATA_USER           — логин пользователя 1С
  ODATA_PASSWORD       — пароль пользователя 1С
  ODATA_TIMEOUT        — таймаут HTTP-запроса, сек (по умолчанию 30)
  ODATA_CONNECT_TIMEOUT — таймаут подключения, сек (по умолчанию 10)

Запуск:
  python mcp_servers/odata_server.py
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any
from urllib.parse import urlparse

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    TextContent,
    Tool,
)

# Добавляем корневую директорию проекта (на уровень выше) в пути поиска Python
# Это позволит найти пакет 'bot_lib', который лежит рядом с 'mcp_servers'
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot_lib.exceptions import ODataConnectionError, ODataHTTPError
from bot_lib.odata_client import ODataClient

log = logging.getLogger("1c-odata-mcp")

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------

ODATA_URL = os.environ.get("ODATA_URL", "").rstrip("/")
ODATA_USER = os.environ.get("ODATA_USER", "")
ODATA_PASSWORD = os.environ.get("ODATA_PASSWORD", "")
ODATA_TIMEOUT = float(os.environ.get("ODATA_TIMEOUT", "30"))
ODATA_CONNECT_TIMEOUT = float(os.environ.get("ODATA_CONNECT_TIMEOUT", "10"))

if not ODATA_URL:
    log.warning("ODATA_URL не задан — запросы не будут работать")

# ---------------------------------------------------------------------------
# Shared ODataClient (initialized lazily)
# ---------------------------------------------------------------------------

_client: ODataClient | None = None


async def _get_client() -> ODataClient:
    """Получить или создать общий ODataClient."""
    global _client
    if _client is None:
        _client = ODataClient(
            base_url=ODATA_URL,
            username=ODATA_USER,
            password=ODATA_PASSWORD,
            timeout=int(ODATA_TIMEOUT),
            verify_ssl=False,
        )
    return _client


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


def _extract_relative_url(full_url: str, base_url: str) -> tuple[str, dict[str, str] | None]:
    """Извлечь относительный путь и query-параметры из полного URL.

    Returns:
        Кортеж ``(path, query_params_dict_or_None)``.
    """
    parsed = urlparse(full_url)
    path = parsed.path

    # Убрать базовый путь если URL начинается с него
    base_parsed = urlparse(base_url)
    base_path = base_parsed.path.rstrip("/")
    if path.startswith(base_path):
        path = path[len(base_path):]
    if not path.startswith("/"):
        path = "/" + path

    # Разобрать query-параметры
    query_params: dict[str, str] | None = None
    if parsed.query:
        from urllib.parse import parse_qs
        qs = parse_qs(parsed.query, keep_blank_values=True)
        query_params = {k: v[0] for k, v in qs.items()}

    return path, query_params


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

    log.info("fetch %s %s", method, url[:200])

    try:
        client = await _get_client()

        # Если URL начинается с base_url — используем relative path
        if url.startswith(ODATA_URL):
            path, query_params = _extract_relative_url(url, ODATA_URL)
            response = await client.raw_request(
                method,
                path,
                params=query_params,
                json_data=json.loads(body) if body else None,
                headers=extra_headers or None,
            )
            resp_text = response.text
            status_code = response.status_code
        else:
            # Полный URL — прямой запрос через httpx (с авторизацией клиента)
            import httpx
            response = await client._client.request(
                method,
                url,
                headers=extra_headers or None,
                content=body,
            )
            resp_text = response.text
            status_code = response.status_code

        # Error status → isError=True
        if status_code >= 400:
            error_msg = f"HTTP {status_code}"
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

    except ODataHTTPError as e:
        return _error_result(f"HTTP {e.status_code}: {e}")
    except ODataConnectionError as e:
        return _error_result(f"Ошибка соединения: {e}")
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