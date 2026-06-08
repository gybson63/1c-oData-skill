#!/usr/bin/env python3
"""Агент-аналитик метаданных 1С."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from openai import AsyncOpenAI

from bot.agents.analyst.analyzer import AnalystService
from bot.agents.base import BaseAgent
from bot.config import get_settings, parse_analyst_settings
from bot.mcp_config import resolve_mcp_config
from bot.messages import AgentProcessResult
from bot.utils import RateLimiter

if TYPE_CHECKING:
    from bot.mcp_client import MCPClientManager

log = logging.getLogger(__name__)


class AnalystAgent(BaseAgent):
    """Анализ объектов метаданных конфигурации 1С."""

    name = "analyst"

    def __init__(self) -> None:
        super().__init__()
        self._ai_client: AsyncOpenAI | None = None
        self._mcp_manager: MCPClientManager | None = None
        self._service: AnalystService | None = None
        self._model: str = ""
        self._settings = parse_analyst_settings(None)

    @property
    def service(self) -> AnalystService | None:
        return self._service

    async def initialize(
        self,
        agent_config: dict[str, Any],
        global_config: dict[str, Any],
        cache_dir: str = ".cache",
        env_file: str = "env.json",
    ) -> None:
        del cache_dir, env_file  # unused

        self._settings = parse_analyst_settings(agent_config)
        settings = get_settings()
        ai = settings.ai
        self._model = ai.model

        self._ai_client = AsyncOpenAI(
            api_key=ai.api_key,
            base_url=ai.base_url,
            max_retries=0,
        )

        profile_cfg = global_config.get("profile_config") or {}
        mcp_config = resolve_mcp_config(profile_cfg, agent_config)
        if mcp_config:
            from bot.mcp_client import MCPClientManager

            self._mcp_manager = MCPClientManager()
            await self._mcp_manager.connect_all(mcp_config)
            if self._mcp_manager.is_connected():
                for srv, info in self._mcp_manager.get_status().items():
                    log.info(
                        "AnalystAgent MCP [%s]: transport=%s, tools=%s",
                        srv,
                        info["transport"],
                        info["tools"],
                    )

        rate_limiter = RateLimiter(rpm=ai.rpm)
        self._service = AnalystService(
            client=self._ai_client,
            model=self._model,
            settings=self._settings,
            mcp_manager=self._mcp_manager,
            rate_limiter=rate_limiter,
            temperature=ai.temperature,
        )
        self._initialized = True
        log.info("AnalystAgent инициализирован (MCP=%s)", bool(self._mcp_manager and self._mcp_manager.is_connected()))

    async def shutdown(self) -> None:
        if self._mcp_manager:
            await self._mcp_manager.disconnect_all()
            self._mcp_manager = None
        self._service = None
        self._initialized = False
        log.info("AnalystAgent остановлен")

    async def refresh(self) -> None:
        pass

    async def process_message(
        self,
        user_text: str,
        history: list[dict[str, str]],
        *,
        chat_id: int | None = None,
    ) -> AgentProcessResult:
        if not self._service:
            raise RuntimeError("AnalystAgent не инициализирован")

        brief = await self._service.analyze(user_text, chat_id=chat_id)
        answer = brief.to_html_summary()
        new_history = list(history)
        new_history.append({"role": "user", "content": user_text})
        new_history.append({"role": "assistant", "content": answer})
        return AgentProcessResult(
            text=answer,
            history=new_history,
            skip_formatter=True,
        )

    def get_status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "initialized": self._initialized,
            "mcp_connected": self._mcp_manager.is_connected() if self._mcp_manager else False,
            "model": self._model,
            "profile_path": self._settings.profile_path,
            "preprocessor_for_odata": self._settings.preprocessor_for_odata,
        }
