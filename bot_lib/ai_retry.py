#!/usr/bin/env python3
"""Retry logic for OpenAI-compatible chat.completions on timeout."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from openai import APITimeoutError, AsyncOpenAI, BadRequestError

from bot_lib.http_log import log_http_error, log_http_start, log_http_success

log = logging.getLogger(__name__)


def is_ai_timeout_error(exc: BaseException) -> bool:
    """Return True if the exception indicates an AI request timeout."""
    if isinstance(exc, (APITimeoutError, TimeoutError, httpx.TimeoutException)):
        return True
    msg = str(exc).lower()
    return "timeout" in msg or "timed out" in msg


def _ai_chat_url(client: AsyncOpenAI) -> str:
    base = str(client.base_url).rstrip("/")
    return f"{base}/chat/completions"


async def chat_completions_with_retry(
    client: AsyncOpenAI,
    *,
    step: str,
    model: str,
    retry_count: int,
    retry_delay: int,
    **kwargs: Any,
):
    """Call chat.completions.create with HTTP logging and timeout retries."""
    url = _ai_chat_url(client)
    max_attempts = retry_count + 1
    last_exc: BaseException | None = None

    for attempt in range(1, max_attempts + 1):
        started_at = log_http_start(
            service="ai",
            method="POST",
            url=url,
            endpoint="chat.completions",
            model=model,
            step=step,
            attempt=attempt,
            max_attempts=max_attempts,
        )
        try:
            resp = await client.chat.completions.create(model=model, **kwargs)  # type: ignore[union-attr]
        except BadRequestError:
            raise
        except Exception as exc:
            last_exc = exc
            log_http_error(
                service="ai",
                method="POST",
                url=url,
                error=exc,
                started_at=started_at,
                endpoint="chat.completions",
                model=model,
                step=step,
                attempt=attempt,
                max_attempts=max_attempts,
            )
            if is_ai_timeout_error(exc) and attempt < max_attempts:
                log.warning(
                    "AI timeout on step=%s (attempt %d/%d), retry in %ds: %s",
                    step,
                    attempt,
                    max_attempts,
                    retry_delay,
                    exc,
                )
                await asyncio.sleep(retry_delay)
                continue
            raise

        log_http_success(
            service="ai",
            method="POST",
            url=url,
            started_at=started_at,
            endpoint="chat.completions",
            model=model,
            step=step,
            attempt=attempt,
        )
        if attempt > 1:
            log.info("AI request succeeded on step=%s after attempt %d/%d", step, attempt, max_attempts)
        return resp

    assert last_exc is not None
    raise last_exc
