#!/usr/bin/env python3
"""Базовый класс для агентов бота.

Каждый агент имеет свои:
  - MCP-серверы (свои подключения)
  - AI-клиент (или использует общий)
  - Системные промпты
  - Кэш и состояние
"""

from __future__ import annotations

import abc
from typing import Any


class BaseAgent(abc.ABC):
    """Абстрактный базовый класс агента."""

    name: str = "base"

    def __init__(self) -> None:
        self._initialized: bool = False

    # -- lifecycle -----------------------------------------------------------

    @abc.abstractmethod
    async def initialize(
        self,
        agent_config: dict[str, Any],
        global_config: dict[str, Any],
        cache_dir: str = ".cache",
        env_file: str = "env.json",
    ) -> None:
        """Инициализация агента: подключение MCP, загрузка данных, создание AI-клиента.

        Args:
            agent_config: настройки агента из env.json (секция agents.<name>)
            global_config: общие настройки профиля env.json
            cache_dir: директория для кэша
            env_file: путь к env.json
        """
        ...

    @abc.abstractmethod
    async def shutdown(self) -> None:
        """Корректное завершение: отключение MCP, освобождение ресурсов."""
        ...

    @abc.abstractmethod
    async def refresh(self) -> None:
        """Обновление данных агента (метаданные, промпты и т.д.)."""
        ...

    # -- processing ----------------------------------------------------------

    @abc.abstractmethod
    async def process_message(
        self,
        user_text: str,
        history: list[dict[str, str]],
        *,
        chat_id: int | None = None,
    ) -> tuple[str, list[dict[str, str]]]:
        """Обработать сообщение пользователя.

        Args:
            user_text: текст сообщения от пользователя
            history: история диалога (список {role, content})
            chat_id: ID чата для трекинга токенов по сессии

        Returns:
            Кортеж (answer, updated_history):
              - answer: текст ответа (HTML для Telegram)
              - updated_history: обновлённая история диалога
        """
        ...

    # -- status --------------------------------------------------------------

    @abc.abstractmethod
    def get_status(self) -> dict[str, Any]:
        """Вернуть словарь с информацией о состоянии агента."""
        ...

    @property
    def is_initialized(self) -> bool:
        return self._initialized
