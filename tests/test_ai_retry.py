#!/usr/bin/env python3
"""Tests for AI timeout retry logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai import APITimeoutError

from bot_lib.ai_retry import chat_completions_with_retry, is_ai_timeout_error


class TestIsAiTimeoutError:
    def test_api_timeout_error(self):
        assert is_ai_timeout_error(APITimeoutError("Request timed out."))

    def test_message_heuristic(self):
        assert is_ai_timeout_error(Exception("Request timed out."))

    def test_non_timeout(self):
        assert not is_ai_timeout_error(Exception("Invalid API key"))


class TestChatCompletionsWithRetry:
    @pytest.mark.asyncio
    async def test_retries_on_timeout_then_succeeds(self):
        client = MagicMock()
        client.base_url = "https://api.openai.com/v1"
        success_resp = MagicMock()
        client.chat.completions.create = AsyncMock(
            side_effect=[
                APITimeoutError("Request timed out."),
                success_resp,
            ]
        )

        with patch("bot_lib.ai_retry.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            resp = await chat_completions_with_retry(
                client,
                step="step1",
                model="gpt-4o-mini",
                retry_count=2,
                retry_delay=3,
                messages=[{"role": "user", "content": "hi"}],
            )

        assert resp is success_resp
        assert client.chat.completions.create.await_count == 2
        sleep_mock.assert_awaited_once_with(3)

    @pytest.mark.asyncio
    async def test_raises_after_all_retries_exhausted(self):
        client = MagicMock()
        client.base_url = "https://api.openai.com/v1"
        client.chat.completions.create = AsyncMock(side_effect=APITimeoutError("Request timed out."))

        with patch("bot_lib.ai_retry.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(APITimeoutError):
                await chat_completions_with_retry(
                    client,
                    step="step1",
                    model="gpt-4o-mini",
                    retry_count=1,
                    retry_delay=2,
                    messages=[{"role": "user", "content": "hi"}],
                )

        assert client.chat.completions.create.await_count == 2

    @pytest.mark.asyncio
    async def test_does_not_retry_non_timeout_errors(self):
        client = MagicMock()
        client.base_url = "https://api.openai.com/v1"
        client.chat.completions.create = AsyncMock(side_effect=RuntimeError("server error"))

        with patch("bot_lib.ai_retry.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            with pytest.raises(RuntimeError, match="server error"):
                await chat_completions_with_retry(
                    client,
                    step="step1",
                    model="gpt-4o-mini",
                    retry_count=2,
                    retry_delay=3,
                    messages=[{"role": "user", "content": "hi"}],
                )

        assert client.chat.completions.create.await_count == 1
        sleep_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_passes_model_to_create(self):
        client = MagicMock()
        client.base_url = "https://api.openai.com/v1"
        success_resp = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=success_resp)

        await chat_completions_with_retry(
            client,
            step="step1",
            model="gpt-4o-mini",
            retry_count=0,
            retry_delay=1,
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.1,
        )

        client.chat.completions.create.assert_awaited_once_with(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.1,
        )
