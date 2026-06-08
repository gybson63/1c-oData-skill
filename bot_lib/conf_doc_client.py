#!/usr/bin/env python3
"""HTTP-клиент для API 1c-conf-doc."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from bot_lib.exceptions import ODataSkillError

log = logging.getLogger(__name__)


class ConfDocApiError(ODataSkillError):
    """Ошибка HTTP API conf-doc."""


class ConfDocClient:
    """Асинхронный клиент conf-doc REST API."""

    def __init__(
        self,
        api_url: str,
        configuration: str = "",
        timeout: float = 60.0,
    ) -> None:
        self._base = api_url.rstrip("/")
        self._configuration = configuration
        self._timeout = timeout

    async def health(self) -> dict[str, Any]:
        data = await self._request("GET", "/health")
        return data if isinstance(data, dict) else {}

    async def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"query": query, "top_k": top_k}
        if self._configuration:
            payload["configuration"] = self._configuration
        data = await self._request("POST", "/search", json=payload)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            results = data.get("results") or data.get("items")
            if isinstance(results, list):
                return results
        return []

    async def get_object(self, object_type: str, name: str) -> dict[str, Any]:
        path = f"/objects/{object_type}/{name}"
        data = await self._request("GET", path)
        return data if isinstance(data, dict) else {}

    async def get_object_chunk(self, object_type: str, name: str, chunk_index: int) -> dict[str, Any]:
        path = f"/objects/{object_type}/{name}/chunks/{chunk_index}"
        data = await self._request("GET", path)
        return data if isinstance(data, dict) else {}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self._base}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.request(method, url, json=json)
                resp.raise_for_status()
                if not resp.content:
                    return {}
                return resp.json()
        except httpx.HTTPError as exc:
            raise ConfDocApiError(str(exc)) from exc
