#!/usr/bin/env python3
"""MCP-сервер для выполнения HTTP-запросов к 1С OData API.

Предоставляет инструменты:
  - fetch — произвольный HTTP-запрос к OData
  - fetch_table — данные сущности в CSV/JSON
  - analyze_data — multi-query, join, aggregate, график

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
import base64
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

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.agents.odata.analytics_executor import AnalyticsExecutor  # noqa: E402
from bot.agents.odata.analytics_models import AnalyticsPlan  # noqa: E402
from bot.agents.odata.chart_renderer import render_html, render_png  # noqa: E402
from bot.agents.odata.query_executor import QueryExecutor  # noqa: E402
from bot.agents.odata.response_parser import resolve_references  # noqa: E402
from bot_lib.dataframe import dataframe_to_csv, records_to_dataframe  # noqa: E402
from bot_lib.exceptions import ODataConnectionError, ODataHTTPError  # noqa: E402
from bot_lib.http_log import log_http_error  # noqa: E402
from bot_lib.odata_client import ODataClient  # noqa: E402

log = logging.getLogger("1c-odata-mcp")

ODATA_URL = os.environ.get("ODATA_URL", "").rstrip("/")
ODATA_USER = os.environ.get("ODATA_USER", "")
ODATA_PASSWORD = os.environ.get("ODATA_PASSWORD", "")
ODATA_TIMEOUT = float(os.environ.get("ODATA_TIMEOUT", "30"))
ODATA_CONNECT_TIMEOUT = float(os.environ.get("ODATA_CONNECT_TIMEOUT", "10"))
MAX_ANALYTICS_RECORDS = int(os.environ.get("ODATA_MAX_ANALYTICS_RECORDS", "500"))
MAX_ANALYTICS_JOINS = int(os.environ.get("ODATA_MAX_ANALYTICS_JOINS", "3"))
CHART_MAX_CATEGORIES = int(os.environ.get("ODATA_CHART_MAX_CATEGORIES", "30"))

if not ODATA_URL:
    log.warning("ODATA_URL не задан — запросы не будут работать")

_client: ODataClient | None = None
_executor: QueryExecutor | None = None


def _auth_header() -> str:
    token = base64.b64encode(f"{ODATA_USER}:{ODATA_PASSWORD}".encode()).decode()
    return f"Basic {token}"


async def _get_client() -> ODataClient:
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


def _get_query_executor() -> QueryExecutor:
    global _executor
    if _executor is None:
        _executor = QueryExecutor(
            odata_url=ODATA_URL,
            auth_header=_auth_header(),
            request_timeout=int(ODATA_TIMEOUT),
        )
    return _executor


app = Server("1c-odata")


@app.list_tools()
async def list_tools() -> list[Tool]:
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
        ),
        Tool(
            name="fetch_table",
            description=(
                "Загрузить записи OData-сущности и вернуть таблицу в CSV или JSON. Удобно для работы с pandas в Cursor."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "entity": {"type": "string", "description": "Имя сущности OData, например Catalog_Контрагенты"},
                    "filter": {"type": "string", "description": "Выражение $filter"},
                    "select": {"type": "string", "description": "Поля $select через запятую"},
                    "orderby": {"type": "string", "description": "Поля $orderby"},
                    "top": {"type": "integer", "description": "Лимит записей ($top)", "default": 200},
                    "skip": {"type": "integer", "description": "Смещение ($skip)", "default": 0},
                    "expand": {"type": "string", "description": "Навигационные свойства $expand"},
                    "format": {
                        "type": "string",
                        "enum": ["csv", "json"],
                        "default": "csv",
                        "description": "Формат ответа",
                    },
                },
                "required": ["entity"],
            },
        ),
        Tool(
            name="analyze_data",
            description=(
                "Multi-query analytics: несколько OData-запросов, join в pandas, "
                "агрегация и опционально график (PNG base64 + plotly HTML)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "description": "Список OData-запросов с alias",
                        "items": {"type": "object"},
                    },
                    "joins": {
                        "type": "array",
                        "description": "Спецификации join между alias",
                        "items": {"type": "object"},
                    },
                    "aggregate": {
                        "type": "object",
                        "description": "group_by и agg для pandas",
                    },
                    "chart": {
                        "type": "object",
                        "description": "Спецификация графика: type, x, y, title",
                    },
                    "explanation": {"type": "string"},
                },
                "required": ["queries"],
            },
        ),
    ]


def _error_result(msg: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=msg)], isError=True)


def _success_result(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], isError=False)


def _extract_relative_url(full_url: str, base_url: str) -> tuple[str, dict[str, str] | None]:
    parsed = urlparse(full_url)
    path = parsed.path
    base_parsed = urlparse(base_url)
    base_path = base_parsed.path.rstrip("/")
    if path.startswith(base_path):
        path = path[len(base_path) :]
    if not path.startswith("/"):
        path = "/" + path

    query_params: dict[str, str] | None = None
    if parsed.query:
        from urllib.parse import parse_qs

        qs = parse_qs(parsed.query, keep_blank_values=True)
        query_params = {k: v[0] for k, v in qs.items()}

    return path, query_params


async def _handle_fetch(arguments: dict[str, Any]) -> CallToolResult:
    url = arguments.get("url", "")
    method = arguments.get("method", "GET").upper()
    extra_headers = arguments.get("headers", {})
    body = arguments.get("body")

    if not url:
        return _error_result("Ошибка: не указан URL")

    log.info("fetch %s %s", method, url[:200])

    try:
        client = await _get_client()

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
            response = await client._client.request(
                method,
                url,
                headers=extra_headers or None,
                content=body,
            )
            resp_text = response.text
            status_code = response.status_code

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
            log_http_error(
                service="odata-mcp",
                method=method,
                url=url,
                error=ODataHTTPError(error_msg, status_code=status_code, url=url),
                status_code=status_code,
                response_body=resp_text[:200],
            )
            return _error_result(error_msg)

        return _success_result(resp_text)

    except ODataHTTPError as e:
        log_http_error(
            service="odata-mcp",
            method=method,
            url=url,
            error=e,
            status_code=e.status_code,
        )
        return _error_result(f"HTTP {e.status_code}: {e}")
    except ODataConnectionError as e:
        log_http_error(
            service="odata-mcp",
            method=method,
            url=url,
            error=e,
        )
        return _error_result(f"Ошибка соединения: {e}")
    except Exception as e:
        log.exception("fetch error")
        return _error_result(f"Ошибка: {e}")


async def _handle_fetch_table(arguments: dict[str, Any]) -> CallToolResult:
    entity = arguments.get("entity", "")
    if not entity:
        return _error_result("Ошибка: не указан entity")

    fmt = arguments.get("format", "csv")
    top = int(arguments.get("top") or 200)
    skip = int(arguments.get("skip") or 0)

    try:
        executor = _get_query_executor()
        records, total = await executor.execute(
            entity=entity,
            filter_expr=arguments.get("filter"),
            select=arguments.get("select"),
            orderby=arguments.get("orderby"),
            top=min(top, MAX_ANALYTICS_RECORDS),
            skip=skip or None,
            expand=arguments.get("expand"),
        )
        resolved = resolve_references(records)
        df = records_to_dataframe(resolved)

        if fmt == "json":
            payload = df.to_json(orient="records", force_ascii=False, indent=2)
        else:
            payload = dataframe_to_csv(df)

        header = f"# entity={entity} rows={len(df)} total={total}\n"
        return _success_result(header + payload)

    except Exception as e:
        log.exception("fetch_table error")
        return _error_result(f"Ошибка fetch_table: {e}")


async def _handle_analyze_data(arguments: dict[str, Any]) -> CallToolResult:
    try:
        plan_data = {"mode": "analytics", **arguments}
        plan = AnalyticsPlan.from_dict(plan_data)
        analytics_exec = AnalyticsExecutor(
            _get_query_executor(),
            max_records=MAX_ANALYTICS_RECORDS,
            max_joins=MAX_ANALYTICS_JOINS,
        )
        result = await analytics_exec.execute(plan)
        df = result.dataframe

        out: dict[str, Any] = {
            "rows": len(df),
            "columns": list(df.columns),
            "row_counts": result.row_counts,
            "csv_preview": dataframe_to_csv(df.head(min(100, len(df)))),
            "explanation": plan.explanation,
        }

        if plan.chart:
            png_bytes = render_png(df, plan.chart, max_categories=CHART_MAX_CATEGORIES)
            out["chart_png_base64"] = base64.b64encode(png_bytes).decode("ascii")
            out["chart_html"] = render_html(df, plan.chart, max_categories=CHART_MAX_CATEGORIES)

        return _success_result(json.dumps(out, ensure_ascii=False, indent=2))

    except Exception as e:
        log.exception("analyze_data error")
        return _error_result(f"Ошибка analyze_data: {e}")


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    if name == "fetch":
        return await _handle_fetch(arguments)
    if name == "fetch_table":
        return await _handle_fetch_table(arguments)
    if name == "analyze_data":
        return await _handle_analyze_data(arguments)
    return _error_result(f"Неизвестный инструмент: {name}")


async def main():
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
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
