#!/usr/bin/env python3
"""Telegram HTTP transport with structured request/error logging."""

from __future__ import annotations

import httpx
from telegram._utils.defaultvalue import DefaultValue
from telegram._utils.types import ODVInput
from telegram.error import NetworkError, TimedOut
from telegram.request import HTTPXRequest
from telegram.request._baserequest import BaseRequest
from telegram.request._requestdata import RequestData

from bot_lib.http_log import extract_telegram_method, log_http_error, log_http_start, log_http_success


class LoggingHTTPXRequest(HTTPXRequest):
    """HTTPXRequest that logs method, URL, endpoint and errors on each call."""

    async def do_request(
        self,
        url: str,
        method: str,
        request_data: RequestData | None = None,
        read_timeout: ODVInput[float] = BaseRequest.DEFAULT_NONE,
        write_timeout: ODVInput[float] = BaseRequest.DEFAULT_NONE,
        connect_timeout: ODVInput[float] = BaseRequest.DEFAULT_NONE,
        pool_timeout: ODVInput[float] = BaseRequest.DEFAULT_NONE,
    ) -> tuple[int, bytes]:
        if self._client.is_closed:
            raise RuntimeError("This HTTPXRequest is not initialized!")

        files = request_data.multipart_data if request_data else None
        data = request_data.json_parameters if request_data else None

        if isinstance(read_timeout, DefaultValue):
            read_timeout = self._client.timeout.read
        if isinstance(connect_timeout, DefaultValue):
            connect_timeout = self._client.timeout.connect
        if isinstance(pool_timeout, DefaultValue):
            pool_timeout = self._client.timeout.pool
        if isinstance(write_timeout, DefaultValue):
            write_timeout = self._client.timeout.write if not files else self._media_write_timeout

        endpoint = extract_telegram_method(url)
        started_at = log_http_start(
            service="telegram",
            method=method,
            url=url,
            endpoint=endpoint,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            write_timeout=write_timeout,
            pool_timeout=pool_timeout,
        )

        timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=write_timeout,
            pool=pool_timeout,
        )

        try:
            res = await self._client.request(
                method=method,
                url=url,
                headers={"User-Agent": self.USER_AGENT},
                timeout=timeout,
                files=files,
                data=data,
            )
        except httpx.TimeoutException as err:
            if isinstance(err, httpx.PoolTimeout):
                log_http_error(
                    service="telegram",
                    method=method,
                    url=url,
                    error=err,
                    started_at=started_at,
                    endpoint=endpoint,
                    connect_timeout=connect_timeout,
                    read_timeout=read_timeout,
                    pool_timeout=pool_timeout,
                    hint="connection pool exhausted; request was not sent to Telegram",
                )
            else:
                log_http_error(
                    service="telegram",
                    method=method,
                    url=url,
                    error=err,
                    started_at=started_at,
                    endpoint=endpoint,
                    connect_timeout=connect_timeout,
                    read_timeout=read_timeout,
                    write_timeout=write_timeout,
                )
            if isinstance(err, httpx.PoolTimeout):
                raise TimedOut(
                    message=(
                        "Pool timeout: All connections in the connection pool are occupied. "
                        "Request was *not* sent to Telegram. Consider adjusting the connection "
                        "pool size or the pool timeout."
                    )
                ) from err
            raise TimedOut from err
        except httpx.HTTPError as err:
            hint = None
            if isinstance(err, httpx.ConnectError):
                hint = (
                    "Не удалось подключиться к Telegram API (api.telegram.org). "
                    "Проверьте интернет, VPN/прокси и telegram.proxy_url в env.json."
                )
            log_http_error(
                service="telegram",
                method=method,
                url=url,
                error=err,
                started_at=started_at,
                endpoint=endpoint,
                connect_timeout=connect_timeout,
                read_timeout=read_timeout,
                write_timeout=write_timeout,
                hint=hint,
            )
            raise NetworkError(f"httpx.{err.__class__.__name__}: {err}") from err

        log_http_success(
            service="telegram",
            method=method,
            url=url,
            started_at=started_at,
            status_code=res.status_code,
            endpoint=endpoint,
        )
        return res.status_code, res.content
