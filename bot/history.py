#!/usr/bin/env python3
"""Управление историей диалогов с ограничением размера и персистентностью.

Решает две проблемы:
  1. Неограниченный рост ``dict[int, list]`` в памяти → утечка памяти
  2. Потеря истории при перезапуске бота

Использование::

    from bot.history import HistoryManager

    mgr = HistoryManager(persist_dir=".cache/histories")

    # Получить историю (подгрузится с диска при первом обращении)
    history = mgr.get(chat_id)

    # Сохранить после обработки
    mgr.save(chat_id, updated_history)

    # Очистить
    mgr.clear(chat_id)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Значения по умолчанию (могут быть переопределены через HistorySettings)
DEFAULT_MAX_MESSAGES = 100   # абсолютный максимум сообщений на чат
DEFAULT_TRIM_TO = 60         # при достижении MAX — обрезать до этого числа


class HistoryManager:
    """Управление историей диалогов с ограничением размера и сохранением на диск.

    Параметры:
        max_messages: абсолютный максимум сообщений на чат (safety net).
            Agent уже обрезает историю до ``history_max_turns * 2`` для AI-контекста,
            а это — защита от неограниченного роста.
        trim_to: количество сообщений, оставляемых при обрезке.
            Сохраняет все ``system``-сообщения + последние N обычных.
        persist_dir: директория для сохранения историй на диск.
            ``None`` — только в памяти (без персистентности).
    """

    def __init__(
        self,
        max_messages: int = DEFAULT_MAX_MESSAGES,
        trim_to: int = DEFAULT_TRIM_TO,
        persist_dir: Optional[str] = None,
    ) -> None:
        self._max = max_messages
        self._trim_to = min(trim_to, max_messages)
        self._persist_dir = Path(persist_dir) if persist_dir else None
        self._histories: dict[int, list[dict[str, str]]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, chat_id: int) -> list[dict[str, str]]:
        """Получить историю чата.

        При первом обращении к чату подгружает историю с диска (если
        включена персистентность).  Возвращает ссылку на внутренний список.
        """
        if chat_id not in self._histories:
            self._histories[chat_id] = self._load_from_disk(chat_id)
        return self._histories[chat_id]

    def save(self, chat_id: int, history: list[dict[str, str]]) -> None:
        """Сохранить обновлённую историю чата.

        Вызывается после того, как агент вернул ``(answer, updated_history)``.
        При превышении ``max_messages`` автоматически обрезает историю.
        """
        # Обрезка при превышении лимита
        if len(history) > self._max:
            history = self._trim(history)
            logger.info(
                "History trimmed for chat %s: %d → %d messages",
                chat_id, len(self._histories.get(chat_id, [])), len(history),
            )

        self._histories[chat_id] = history
        self._save_to_disk(chat_id)

    def append(self, chat_id: int, message: dict[str, str]) -> None:
        """Добавить одно сообщение в историю с автоматической обрезкой.

        Удобный метод для случаев, когда нужно добавить сообщение
        без прохождения через агента (например, при ошибке).
        """
        history = self.get(chat_id)
        history.append(message)
        # Делегируем в save() — там есть обрезка при необходимости
        self.save(chat_id, history)

    def clear(self, chat_id: int) -> None:
        """Полностью очистить историю чата (память + диск)."""
        self._histories.pop(chat_id, None)
        self._delete_from_disk(chat_id)
        logger.info("History cleared for chat %s", chat_id)

    def chat_count(self) -> int:
        """Количество чатов с историей (в памяти)."""
        return len(self._histories)

    def total_messages(self) -> int:
        """Общее количество сообщений во всех чатах."""
        return sum(len(h) for h in self._histories.values())

    # ------------------------------------------------------------------
    # Private: trimming
    # ------------------------------------------------------------------

    def _trim(self, history: list[dict[str, str]]) -> list[dict[str, str]]:
        """Обрезать историю, сохранив system-сообщения + последние N."""
        system_msgs = [m for m in history if m.get("role") == "system"]
        other_msgs = [m for m in history if m.get("role") != "system"]
        trimmed = system_msgs + other_msgs[-self._trim_to:]
        return trimmed

    # ------------------------------------------------------------------
    # Private: disk persistence
    # ------------------------------------------------------------------

    def _path_for(self, chat_id: int) -> Path:
        """Путь к файлу истории чата на диске."""
        assert self._persist_dir is not None
        return self._persist_dir / f"history_{chat_id}.json"

    def _load_from_disk(self, chat_id: int) -> list[dict[str, str]]:
        """Загрузить историю с диска (если доступна)."""
        if not self._persist_dir:
            return []

        path = self._path_for(chat_id)
        if not path.is_file():
            return []

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                msg_count = len(data)
                logger.info(
                    "Loaded history for chat %s from disk (%d messages)",
                    chat_id, msg_count,
                )
                return data
            logger.warning(
                "Invalid history format for chat %s (expected list), starting fresh",
                chat_id,
            )
            return []
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse history for chat %s: %s", chat_id, e)
        except OSError as e:
            logger.warning("Failed to read history for chat %s: %s", chat_id, e)

        return []

    def _save_to_disk(self, chat_id: int) -> None:
        """Сохранить историю чата на диск."""
        if not self._persist_dir:
            return

        try:
            self._persist_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("Failed to create history dir %s: %s", self._persist_dir, e)
            return

        path = self._path_for(chat_id)
        history = self._histories.get(chat_id, [])

        try:
            path.write_text(
                json.dumps(history, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("Failed to save history for chat %s: %s", chat_id, e)

    def _delete_from_disk(self, chat_id: int) -> None:
        """Удалить файл истории чата с диска."""
        if not self._persist_dir:
            return

        path = self._path_for(chat_id)
        if path.is_file():
            try:
                path.unlink()
            except OSError as e:
                logger.warning("Failed to delete history for chat %s: %s", chat_id, e)
