#!/usr/bin/env python3
"""Общий асинхронный HTTP-клиент для OData 1С.

Используется:
- ``bot.agents.odata.odata_http`` — выполнение OData-запросов агента
- ``mcp_servers.odata_server`` — MCP-сервер для OData
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import urlencode

from typing import TYPE_CHECKING

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

if TYPE_CHECKING:
    import tenacity

from lib.exceptions import ODataConnectionError, ODataHTTPError

logger = logging.getLogger(__name__)

# Максимальная длина URL для GET-запроса (большинство серверов — 8192)
_MAX_URL_LENGTH = 8192


def _log_retry(retry_state: "tenacity.RetryCallState") -> None:
    """Логировать каждую попытку повтора."""
    logger.warning(
        "Повтор #%d %s — ожидание %.1fs",
        retry_state.attempt_number,
        f"для {retry_state.fn.__name__}" if retry_state.fn else "",
        retry_state.next_action.sleep if retry_state.next_action else 0,
    )


_retry_policy = retry(
    retry=retry_if_exception_type(ODataConnectionError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    before_sleep=_log_retry,
    reraise=True,
)


class ODataClient:
    """Асинхронный HTTP-клиент для OData 1С.

    Контекст-менеджер — корректно закрывает httpx-клиент::

        async with ODataClient(base_url, user, pwd) as client:
            data = await client.get_entities('Catalog_Товары')

    Или ручное закрытие::

        client = ODataClient(base_url, user, pwd)
        try:
            data = await client.get_entities('Catalog_Товары')
        finally:
            await client.close()
    """

    def __init__(
        self,
        base_url: str,
        username: str = "",
        password: str = "",
        timeout: int = 30,
        verify_ssl: bool = True,
        auth_header: str = "",
        max_url_length: int = _MAX_URL_LENGTH,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._verify_ssl = verify_ssl
        self._max_url_length = max_url_length

        auth: Optional[httpx.BasicAuth] = None
        extra_headers: dict[str, str] = {"Accept": "application/json"}

        if username:
            auth = httpx.BasicAuth(username, password)
        elif auth_header:
            extra_headers["Authorization"] = auth_header

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            auth=auth,
            timeout=timeout,
            verify=verify_ssl,
            headers=extra_headers,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_entities(
        self,
        entity: str,
        filter_: Optional[str] = None,
        select: Optional[str] = None,
        orderby: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        expand: Optional[str] = None,
        count: bool = False,
    ) -> dict[str, Any]:
        """Получить сущности из OData.

        Args:
            entity: имя набора сущностей (например ``Catalog_Товары``)
            filter_: OData $filter
            select: OData $select
            orderby: OData $orderby
            top: OData $top
            skip: OData $skip
            expand: OData $expand
            count: добавить $count=true

        Returns:
            JSON-ответ как dict (обычно содержит ``value`` и опционально ``@odata.count``)
        """
        params = self._build_params(
            filter_=filter_,
            select=select,
            orderby=orderby,
            top=top,
            skip=skip,
            expand=expand,
            count=count,
        )
        path = f"/{entity}"
        url = self._safe_url(path, params)
        logger.info("OData GET %s", url)
        return await self._request_json("GET", path, params=params)

    async def get_count(
        self,
        entity: str,
        filter_: Optional[str] = None,
    ) -> int:
        """Получить количество сущностей (``/$count``).

        Returns:
            Целое число — количество записей.
        """
        path = f"/{entity}/$count"
        params: dict[str, Any] = {}
        if filter_:
            params["$filter"] = filter_

        logger.info("OData GET %s", path)
        response = await self._request_raw("GET", path, params=params)
        return int(response.text.strip())

    async def get_metadata(self) -> str:
        """Получить ``$metadata`` XML.

        Returns:
            Строка с XML-содержимым $metadata.
        """
        logger.info("OData GET /$metadata")
        response = await self._request_raw(
            "GET",
            "/$metadata",
            headers={"Accept": "application/xml"},
        )
        return response.text

    async def raw_request(
        self,
        method: str,
        entity: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_data: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> httpx.Response:
        """Универсальный HTTP-запрос к OData.

        Используется MCP-сервером для произвольных запросов.

        Args:
            method: HTTP-метод (GET, POST, PATCH, DELETE)
            entity: путь (entity set name или полный относительный URL)
            params: query-параметры
            json_data: тело запроса (JSON)
            headers: дополнительные заголовки

        Returns:
            ``httpx.Response``
        """
        path = f"/{entity}" if not entity.startswith("/") else entity
        request_headers = dict(headers or {})
        return await self._request_raw(
            method,
            path,
            params=params,
            json_data=json_data,
            headers=request_headers if request_headers else None,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Закрыть HTTP-клиент."""
        await self._client.aclose()

    async def __aenter__(self) -> ODataClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_params(
        filter_: Optional[str] = None,
        select: Optional[str] = None,
        orderby: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        expand: Optional[str] = None,
        count: bool = False,
        format_: Optional[str] = None,
    ) -> dict[str, Any]:
        """Собрать OData query-параметры."""
        params: dict[str, Any] = {}
        if filter_:
            params["$filter"] = filter_
        if select:
            params["$select"] = select
        if orderby:
            params["$orderby"] = orderby
        if top is not None:
            params["$top"] = top
        if skip is not None:
            params["$skip"] = skip
        if expand:
            params["$expand"] = expand
        if count:
            params["$count"] = "true"
        if format_:
            params["$format"] = format_
        return params

    def _safe_url(self, path: str, params: dict[str, Any]) -> str:
        """Собрать URL, обрезав слишком длинный $filter."""
        url = f"{self._base_url}{path}"
        if not params:
            return url

        qs = urlencode(params)
        full = f"{url}?{qs}"
        if len(full) <= _MAX_URL_LENGTH:
            return full

        # Превышен лимит — попробуем убрать $filter
        if "$filter" in params:
            removed_filter = params.pop("$filter")
            qs = urlencode(params)
            full_no_filter = f"{url}?{qs}"
            logger.warning(
                "URL превышает %d символов, $filter убран: %s",
                _MAX_URL_LENGTH,
                removed_filter[:100],
            )
            return full_no_filter

        return full

    @_retry_policy
    async def _request_raw(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_data: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> httpx.Response:
        """Выполнить HTTP-запрос с retry при ошибках соединения.

        Retry применяется только для ``ODataConnectionError`` (timeout, DNS,
        connection refused). HTTP-ошибки (4xx, 5xx) не повторяются.
        """
        try:
            response = await self._client.request(
                method,
                path,
                params=params,
                json=json_data,
                headers=headers,
            )
            response.raise_for_status()
            return response
        except httpx.TimeoutException as exc:
            raise ODataConnectionError(
                f"Timeout при запросе {method} {path}: {exc}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ODataHTTPError(
                message=f"HTTP {exc.response.status_code} для {method} {path}: {exc.response.text[:200]}",
                status_code=exc.response.status_code,
                url=str(exc.request.url),
            ) from exc
        except httpx.RequestError as exc:
            raise ODataConnectionError(
                f"Ошибка соединения при {method} {path}: {exc}"
            ) from exc

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Выполнить запрос и вернуть JSON."""
        response = await self._request_raw(method, path, params=params)
        try:
            return response.json()
        except Exception as exc:
            from lib.exceptions import ODataParseError

            raise ODataParseError(
                f"Не удалось разобрать JSON-ответ: {exc}"
            ) from exc