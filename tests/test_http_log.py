#!/usr/bin/env python3
"""Tests for bot_lib.http_log and LoggingHTTPXRequest."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from telegram.error import NetworkError

from bot.logging_config import setup_logging
from bot.telegram_transport import LoggingHTTPXRequest
from bot_lib.exceptions import ODataConnectionError
from bot_lib.http_log import extract_telegram_method, redact_url
from bot_lib.odata_client import ODataClient


@pytest.fixture
def structured_logging():
    """Route structlog through stdlib so caplog captures HTTP events."""
    setup_logging(level="DEBUG")
    yield
    logging.getLogger().handlers.clear()
    logging.getLogger().setLevel(logging.WARNING)


class TestRedactUrl:
    def test_masks_telegram_bot_token(self):
        url = "https://api.telegram.org/bot123456:ABC-DEF/getMe"
        assert redact_url(url) == "https://api.telegram.org/bot***/getMe"

    def test_masks_basic_auth(self):
        url = "http://admin:secret@localhost/odata/standard.odata"
        assert redact_url(url) == "http://admin:***@localhost/odata/standard.odata"

    def test_masks_password_query_param(self):
        url = "http://localhost/api?user=admin&password=secret&top=10"
        assert "password=***" in redact_url(url)
        assert "top=10" in redact_url(url)


class TestExtractTelegramMethod:
    def test_get_me(self):
        url = "https://api.telegram.org/bot123:ABC/getMe"
        assert extract_telegram_method(url) == "getMe"

    def test_send_message(self):
        url = "https://api.telegram.org/bot123:ABC/sendMessage"
        assert extract_telegram_method(url) == "sendMessage"

    def test_non_telegram_url(self):
        assert extract_telegram_method("https://api.openai.com/v1/chat/completions") is None


class TestLoggingHTTPXRequest:
    @pytest.mark.asyncio
    async def test_connect_error_logs_http_request_failed(self, structured_logging, caplog):
        caplog.set_level(logging.DEBUG, logger="1c-bot.http")

        request = LoggingHTTPXRequest(connect_timeout=5, read_timeout=5, write_timeout=5)
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.timeout = httpx.Timeout(5.0)
        mock_client.request = AsyncMock(side_effect=httpx.ConnectError("All connection attempts failed"))
        request._client = mock_client  # noqa: SLF001

        url = "https://api.telegram.org/bot123:ABC/getMe"
        with pytest.raises(NetworkError):
            await request.do_request(url=url, method="POST")

        assert any("http_request_failed" in r.message for r in caplog.records)
        failed = next(r for r in caplog.records if "http_request_failed" in r.message)
        assert "telegram" in failed.message
        assert "getMe" in failed.message
        assert "ConnectError" in failed.message
        assert "bot***" in failed.message or "bot***/" in failed.message


class TestLogHttpErrorOData:
    @pytest.mark.asyncio
    async def test_odata_connect_error_in_caplog(self, structured_logging, odata_url: str, caplog):
        caplog.set_level(logging.DEBUG, logger="1c-bot.http")

        async with ODataClient(odata_url) as client:
            with patch.object(
                client._client,
                "request",
                new_callable=AsyncMock,
                side_effect=httpx.ConnectError("refused"),
            ):
                with pytest.raises(ODataConnectionError):
                    await client._request_raw("GET", "/Catalog_Test")

        assert any("http_request_failed" in r.message for r in caplog.records)
        failed = next(r for r in caplog.records if "http_request_failed" in r.message)
        assert "odata" in failed.message
        assert "Catalog_Test" in failed.message
        assert odata_url in failed.message or redact_url(odata_url) in failed.message
