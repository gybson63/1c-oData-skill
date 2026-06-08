#!/usr/bin/env python3
"""Structured logging for outbound HTTP requests across all transports."""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

import structlog

HTTP_LOGGER_NAME = "1c-bot.http"

_log = structlog.get_logger(HTTP_LOGGER_NAME)

# Telegram bot token in URL path: /bot<token>/method
_TELEGRAM_BOT_PATH = re.compile(r"(/bot)[^/]+(?=/)")


def redact_url(url: str) -> str:
    """Mask secrets in URLs (bot tokens, Basic Auth, password query params)."""
    if not url:
        return url

    parsed = urlparse(url)
    netloc = parsed.netloc

    if "@" in netloc:
        userinfo, hostport = netloc.rsplit("@", 1)
        if ":" in userinfo:
            user = userinfo.split(":", 1)[0]
            netloc = f"{user}:***@{hostport}"
        else:
            netloc = f"***@{hostport}"

    path = _TELEGRAM_BOT_PATH.sub(r"\1***", parsed.path)

    query = parsed.query
    if query:
        parts: list[str] = []
        for pair in query.split("&"):
            if not pair:
                continue
            key, _, value = pair.partition("=")
            if key.lower() in {"password", "pwd", "token", "api_key", "apikey", "secret"}:
                parts.append(f"{key}=***")
            else:
                parts.append(pair)
        query = "&".join(parts)

    return urlunparse((parsed.scheme, netloc, path, parsed.params, query, parsed.fragment))


def extract_telegram_method(url: str) -> str | None:
    """Extract Telegram Bot API method name from URL (e.g. getMe, sendMessage)."""
    parsed = urlparse(url)
    match = _TELEGRAM_BOT_PATH.search(parsed.path)
    if not match:
        return None
    remainder = parsed.path[match.end() :].lstrip("/")
    if not remainder:
        return None
    return remainder.split("/", 1)[0]


def _base_fields(
    *,
    service: str,
    method: str,
    url: str,
    endpoint: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "service": service,
        "method": method.upper(),
        "url": redact_url(url),
    }
    if endpoint:
        fields["endpoint"] = endpoint
    elif service == "telegram":
        tg_method = extract_telegram_method(url)
        if tg_method:
            fields["endpoint"] = tg_method
    fields.update(extra)
    return fields


def log_http_start(
    *,
    service: str,
    method: str,
    url: str,
    endpoint: str | None = None,
    **extra: Any,
) -> float:
    """Log outbound HTTP request start. Returns monotonic start time."""
    started = time.monotonic()
    _log.debug(
        "http_request_start",
        **_base_fields(service=service, method=method, url=url, endpoint=endpoint, **extra),
    )
    return started


def log_http_success(
    *,
    service: str,
    method: str,
    url: str,
    started_at: float,
    status_code: int | None = None,
    endpoint: str | None = None,
    **extra: Any,
) -> None:
    """Log successful HTTP response (DEBUG level)."""
    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    fields = _base_fields(
        service=service,
        method=method,
        url=url,
        endpoint=endpoint,
        elapsed_ms=elapsed_ms,
        **extra,
    )
    if status_code is not None:
        fields["status_code"] = status_code
    _log.debug("http_request_ok", **fields)


def log_http_error(
    *,
    service: str,
    method: str,
    url: str,
    error: BaseException,
    started_at: float | None = None,
    endpoint: str | None = None,
    status_code: int | None = None,
    response_body: str | None = None,
    **extra: Any,
) -> None:
    """Log failed HTTP request with structured context."""
    fields = _base_fields(
        service=service,
        method=method,
        url=url,
        endpoint=endpoint,
        error_type=type(error).__name__,
        error_message=str(error),
        **extra,
    )
    if started_at is not None:
        fields["elapsed_ms"] = int((time.monotonic() - started_at) * 1000)
    if status_code is not None:
        fields["status_code"] = status_code
    if response_body is not None:
        fields["response_body"] = response_body[:200]
    _log.error("http_request_failed", **fields)
