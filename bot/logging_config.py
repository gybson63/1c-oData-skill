#!/usr/bin/env python3
"""Центральная настройка логирования для проекта.

Поддерживает два режима:
- **Console** (по умолчанию) — удобный для разработчика вывод с цветами
- **JSON** — structured logging через structlog для production/ELK

Переключение через переменную окружения ``LOG_FORMAT=json`` или параметр ``json_format=True``.

Лог-файлы:
- При указании ``log_dir`` создаётся папка с файлами вида
  ``<session_id>_<timestamp>.log``
- При старте генерируется ``session_id`` (UUID4) — общий для всей сессии
- Каждые 10 минут создаётся новый файл с актуальной меткой времени
- Файлы старше 7 дней автоматически удаляются при старте

Использование::

    from bot.logging_config import setup_logging

    # Console + файлы в папке logs/:
    setup_logging(level="INFO", log_dir="logs")

    # Только консоль:
    setup_logging(level="INFO")

    # В любом модуле:
    import logging
    logger = logging.getLogger(__name__)
    logger.info("message", extra={"odata_entity": "Catalog_Товары"})
"""

from __future__ import annotations

import glob
import logging
import logging.handlers
import os
import sys
import threading
import time
import uuid
from datetime import datetime
from io import TextIOWrapper
from pathlib import Path

import structlog
from structlog.stdlib import ProcessorFormatter

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

DEFAULT_LOG_DIR = "logs"
DEFAULT_ROTATE_INTERVAL_SECONDS = 10 * 60  # 10 минут
DEFAULT_CLEANUP_DAYS = 7


# ---------------------------------------------------------------------------
# Session ID — генерируется один раз за запуск процесса
# ---------------------------------------------------------------------------

_session_id: str = uuid.uuid4().hex[:8]


def get_session_id() -> str:
    """Вернуть идентификатор текущей сессии логирования."""
    return _session_id


# ---------------------------------------------------------------------------
# Утилиты для имён файлов
# ---------------------------------------------------------------------------


def _make_log_filename(log_dir: str, session_id: str) -> str:
    """Создать имя лог-файла: ``<log_dir>/<session_id>_<timestamp>.log``."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return os.path.join(log_dir, f"{session_id}_{ts}.log")


def _cleanup_old_logs(log_dir: str, max_age_days: int = DEFAULT_CLEANUP_DAYS) -> None:
    """Удалить лог-файлы старше *max_age_days* дней."""
    cutoff = time.time() - max_age_days * 86400
    pattern = os.path.join(log_dir, "*.log")
    for path in glob.glob(pattern):
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# RotatingSessionFileHandler
# ---------------------------------------------------------------------------


class RotatingSessionFileHandler(logging.Handler):
    """FileHandler, создающий новый файл каждые *rotate_seconds* секунд.

    Имя файла содержит session_id (один на запуск процесса) и метку времени:
    ``<session_id>_<YYYYmmdd_HHMMSS>.log``

    При ротации текущий файл закрывается и открывается новый.
    """

    def __init__(
        self,
        log_dir: str,
        session_id: str,
        rotate_seconds: int = DEFAULT_ROTATE_INTERVAL_SECONDS,
        encoding: str = "utf-8",
    ) -> None:
        super().__init__()
        self._log_dir = log_dir
        self._session_id = session_id
        self._rotate_seconds = rotate_seconds
        self._encoding = encoding
        self._stream: TextIOWrapper | None = None
        self._open_time: float = 0.0
        self._lock = threading.Lock()
        self._current_path: str = ""

        # Создать папку
        Path(log_dir).mkdir(parents=True, exist_ok=True)

        # Открыть первый файл
        self._open_new_file()

        # Запустить фоновый таймер для ротации
        self._timer: threading.Timer | None = None
        self._schedule_rotation()

    # -- Открытие / закрытие файлов ----------------------------------------

    def _open_new_file(self) -> None:
        """Закрыть текущий файл и открыть новый."""
        self._close_stream()
        self._current_path = _make_log_filename(self._log_dir, self._session_id)
        self._stream = open(self._current_path, "a", encoding=self._encoding)  # noqa: SIM115
        self._open_time = time.monotonic()

    def _close_stream(self) -> None:
        """Закрыть текущий поток файла."""
        if self._stream is not None:
            try:
                self._stream.flush()
                self._stream.close()
            except OSError:
                pass
            self._stream = None

    # -- Таймер ротации -----------------------------------------------------

    def _schedule_rotation(self) -> None:
        """Запланировать следующую ротацию."""
        self._timer = threading.Timer(self._rotate_seconds, self._do_rotate)
        self._timer.daemon = True
        self._timer.start()

    def _do_rotate(self) -> None:
        """Выполнить ротацию и запланировать следующую."""
        with self._lock:
            self._open_new_file()
        self._schedule_rotation()

    # -- Handler interface --------------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:
        """Записать лог-запись в текущий файл."""
        with self._lock:
            if self._stream is None:
                return
            try:
                msg = self.format(record) + "\n"
                self._stream.write(msg)
                self._stream.flush()
            except (OSError, ValueError):
                self._close_stream()

    def close(self) -> None:
        """Закрыть handler и остановить таймер."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        with self._lock:
            self._close_stream()
        super().close()

    @property
    def current_path(self) -> str:
        """Путь к текущему файлу лога."""
        return self._current_path


# ---------------------------------------------------------------------------
# DeduplicateFilter
# ---------------------------------------------------------------------------


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
        self._emitting_summary: bool = False  # защита от рекурсии

    def filter(self, record: logging.LogRecord) -> bool:
        # Не фильтровать сводки о подавленных дубликатах
        if self._emitting_summary:
            return True

        key = (record.name, record.levelno, record.getMessage())
        if key == self._last_key:
            self._dup_count += 1
            return False  # подавить дубликат
        # Новое сообщение — вывести сводку о подавленных
        if self._dup_count > 0 and self._last_key is not None:
            self._emitting_summary = True
            try:
                logging.getLogger(self._last_key[0]).log(
                    self._last_key[1],
                    "… повторялось %d раз(а)",
                    self._dup_count,
                )
            finally:
                self._emitting_summary = False
        self._last_key = key
        self._dup_count = 0
        return True


# ---------------------------------------------------------------------------
# structlog processors
# ---------------------------------------------------------------------------

_shared_processors: list = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.stdlib.PositionalArgumentsFormatter(),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.UnicodeDecoder(),
    structlog.processors.TimeStamper(fmt="iso"),
]


def _configure_structlog(json_format: bool = False) -> ProcessorFormatter:
    """Настроить structlog.

    Args:
        json_format: если ``True`` — JSON-вывод (для production/ELK).
                     если ``False`` — dev-console с цветами.

    Returns:
        ProcessorFormatter для stdlib logging handlers.
    """
    if json_format:
        renderer = structlog.processors.JSONRenderer(ensure_ascii=False)
    else:
        renderer = structlog.dev.ConsoleRenderer(
            colors=True,
            exception_formatter=structlog.dev.plain_traceback,
        )

    structlog.configure(
        processors=[
            *_shared_processors,
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=_shared_processors,
    )

    return formatter


# ---------------------------------------------------------------------------
# setup_logging — главная точка входа
# ---------------------------------------------------------------------------


def setup_logging(
    level: str = "INFO",
    log_file: str | None = None,
    log_dir: str | None = None,
    json_format: bool | None = None,
    rotate_seconds: int = DEFAULT_ROTATE_INTERVAL_SECONDS,
    cleanup_days: int = DEFAULT_CLEANUP_DAYS,
) -> None:
    """Настроить логирование во всех модулях.

    Настраивает одновременно:
    - standard ``logging`` (все существующие ``logging.getLogger(__name__)`` работают)
    - ``structlog`` (новый код может использовать ``structlog.get_logger()``)

    Args:
        level: уровень логирования (DEBUG, INFO, WARNING, ERROR)
        log_file: путь к конкретному файлу лога (None = не использовать одиночный файл)
        log_dir: папка для ротируемых лог-файлов с session_id. При указании
                 создаётся :class:`RotatingSessionFileHandler`, который каждые
                 ``rotate_seconds`` секунд создаёт новый файл вида
                 ``<session_id>_<timestamp>.log``.
                 По умолчанию ``"logs"`` если не указан ``log_file``.
        json_format: ``True`` = JSON structured logs, ``False`` = console,
                     ``None`` = авто-определение из ``LOG_FORMAT`` env var
        rotate_seconds: интервал ротации лог-файлов в секундах (по умолчанию 600 = 10 мин)
        cleanup_days: удалять лог-файлы старше этого количества дней (по умолчанию 7)
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Авто-определение формата из окружения
    if json_format is None:
        json_format = os.getenv("LOG_FORMAT", "").lower() == "json"

    # Настроить structlog и получить форматтер для stdlib logging
    formatter = _configure_structlog(json_format=json_format)

    # Создать handlers (каждый handler получает свой экземпляр фильтра,
    # т.к. logging вызывает filter() для каждого handler отдельно)
    handlers: list[logging.Handler] = []

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(_DeduplicateFilter())
    handlers.append(console_handler)

    # RotatingSessionFileHandler — файлы с session_id и ротацией по времени
    effective_log_dir = log_dir if log_dir else DEFAULT_LOG_DIR
    _cleanup_old_logs(effective_log_dir, max_age_days=cleanup_days)

    session_file_handler = RotatingSessionFileHandler(
        log_dir=effective_log_dir,
        session_id=_session_id,
        rotate_seconds=rotate_seconds,
    )
    session_file_handler.setFormatter(formatter)
    session_file_handler.addFilter(_DeduplicateFilter())
    handlers.append(session_file_handler)

    # Одиночный файл (устаревший режим, для обратной совместимости)
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(_DeduplicateFilter())
        handlers.append(file_handler)

    # Настроить root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Удалить старые handlers (для повторных вызовов, например в тестах)
    root_logger.handlers.clear()
    for h in handlers:
        root_logger.addHandler(h)

    # Подавить шумные логи httpx
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Подавить шумные логи telegram.bot
    logging.getLogger("telegram").setLevel(logging.WARNING)

    # Первое сообщение после настройки
    slog = structlog.get_logger("bot.logging_config")
    mode = "JSON" if json_format else "Console"
    slog.info(
        "logging_configured",
        level=level,
        mode=mode,
        log_dir=effective_log_dir,
        session_id=_session_id,
        log_file=session_file_handler.current_path,
    )


def get_structlog(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Получить structlog-логгер.

    Args:
        name: имя логгера (обычно ``__name__``). Если ``None`` —
              structlog определит автоматически.

    Returns:
        BoundLogger с настроенными processors.
    """
    return structlog.get_logger(name)
