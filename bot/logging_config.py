#!/usr/bin/env python3
"""Центральная настройка логирования для проекта."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path


class _DeduplicateFilter(logging.Filter):
    """Фильтр, подавляющий повторяющиеся подряд записи с одинаковым сообщением.

    Сравнение идёт по кортежу (logger_name, level, message) — без timestamp.
    Первое вхождение пропускается, последующие дубликаты — отбрасываются.
    Как только приходит запись с другим сообщением, счётчик дубликатов сбрасывается
    и в лог выводится краткая сводка: «… повторялось N раз».
    """

    def __init__(self) -> None:
        super().__init__()
        self._last_key: tuple[str, int, str] | None = None
        self._dup_count: int = 0

    def filter(self, record: logging.LogRecord) -> bool:
        key = (record.name, record.levelno, record.getMessage())
        if key == self._last_key:
            self._dup_count += 1
            return False  # подавить дубликат
        # Новое сообщение — вывести сводку о подавленных
        if self._dup_count > 0 and self._last_key is not None:
            # Выводим сводку через root logger, чтобы не нарушать логику
            logging.getLogger(self._last_key[0]).log(
                self._last_key[1],
                "… повторялось %d раз(а)",
                self._dup_count,
            )
        self._last_key = key
        self._dup_count = 0
        return True


def setup_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """Настроить логирование во всех модулях.

    Args:
        level: уровень логирования (DEBUG, INFO, WARNING, ERROR)
        log_file: путь к файлу лога (None = только консоль)
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    dedup_filter = _DeduplicateFilter()

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
    ]

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8",
            )
        )

    # Применить фильтр дедупликации ко всем обработчикам
    for h in handlers:
        h.addFilter(dedup_filter)

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )

    # Подавить шумные логи httpx (повторяющиеся HTTP-запросы)
    logging.getLogger("httpx").setLevel(logging.WARNING)
