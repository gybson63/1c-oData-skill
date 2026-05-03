#!/usr/bin/env python3
"""Тесты модуля logging_config: structlog, RotatingSessionFileHandler, DeduplicateFilter."""

from __future__ import annotations

import logging
import os
import time

import pytest

from bot.logging_config import (
    RotatingSessionFileHandler,
    _cleanup_old_logs,
    _DeduplicateFilter,
    _make_log_filename,
    get_session_id,
    get_structlog,
    setup_logging,
)

# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_logging():
    """Сбросить root logger после каждого теста."""
    yield
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)


@pytest.fixture
def tmp_log_dir(tmp_path):
    """Временная директория для лог-файлов."""
    d = tmp_path / "test_logs"
    d.mkdir()
    return str(d)


# ---------------------------------------------------------------------------
# _make_log_filename
# ---------------------------------------------------------------------------


class TestMakeLogFilename:
    def test_format(self):
        path = _make_log_filename("logs", "abcd1234")
        basename = os.path.basename(path)
        assert basename.startswith("abcd1234_")
        assert basename.endswith(".log")

    def test_includes_dir(self):
        path = _make_log_filename("mylogs", "sid")
        assert path.startswith("mylogs")


# ---------------------------------------------------------------------------
# get_session_id
# ---------------------------------------------------------------------------


class TestGetSessionId:
    def test_returns_nonempty_string(self):
        sid = get_session_id()
        assert isinstance(sid, str)
        assert len(sid) == 8


# ---------------------------------------------------------------------------
# RotatingSessionFileHandler
# ---------------------------------------------------------------------------


class TestRotatingSessionFileHandler:
    def test_creates_file_on_init(self, tmp_log_dir):
        handler = RotatingSessionFileHandler(
            log_dir=tmp_log_dir, session_id="test1", rotate_seconds=9999,
        )
        try:
            assert handler.current_path != ""
            assert os.path.isfile(handler.current_path)
            assert "test1" in os.path.basename(handler.current_path)
        finally:
            handler.close()

    def test_writes_log_record(self, tmp_log_dir):
        handler = RotatingSessionFileHandler(
            log_dir=tmp_log_dir, session_id="test2", rotate_seconds=9999,
        )
        try:
            record = logging.LogRecord(
                "test", logging.INFO, "", 0, "hello world", (), None,
            )
            handler.emit(record)

            with open(handler.current_path, encoding="utf-8") as f:
                content = f.read()
            assert "hello world" in content
        finally:
            handler.close()

    def test_rotation_creates_new_file(self, tmp_log_dir):
        handler = RotatingSessionFileHandler(
            log_dir=tmp_log_dir, session_id="test3", rotate_seconds=9999,
        )
        try:
            first_path = handler.current_path
            # Принудительно вызвать ротацию
            handler._do_rotate()
            second_path = handler.current_path

            assert first_path != second_path
            assert os.path.isfile(first_path)
            assert os.path.isfile(second_path)
        finally:
            handler.close()


# ---------------------------------------------------------------------------
# _cleanup_old_logs
# ---------------------------------------------------------------------------


class TestCleanupOldLogs:
    def test_removes_old_files(self, tmp_log_dir):
        # Создать старый файл
        old_file = os.path.join(tmp_log_dir, "old_test.log")
        with open(old_file, "w") as f:
            f.write("old")

        # Установить mtime в прошлое (10 дней назад)
        old_time = time.time() - 10 * 86400
        os.utime(old_file, (old_time, old_time))

        # Создать свежий файл
        new_file = os.path.join(tmp_log_dir, "new_test.log")
        with open(new_file, "w") as f:
            f.write("new")

        _cleanup_old_logs(tmp_log_dir, max_age_days=7)

        assert not os.path.exists(old_file)
        assert os.path.exists(new_file)


# ---------------------------------------------------------------------------
# _DeduplicateFilter
# ---------------------------------------------------------------------------


class TestDeduplicateFilter:
    def test_passes_first_message(self):
        filt = _DeduplicateFilter()
        record = logging.LogRecord("t", logging.INFO, "", 0, "msg", (), None)
        assert filt.filter(record) is True

    def test_suppresses_duplicate(self):
        filt = _DeduplicateFilter()
        rec1 = logging.LogRecord("t", logging.INFO, "", 0, "msg", (), None)
        rec2 = logging.LogRecord("t", logging.INFO, "", 0, "msg", (), None)
        assert filt.filter(rec1) is True
        assert filt.filter(rec2) is False

    def test_passes_after_different_message(self):
        filt = _DeduplicateFilter()
        rec1 = logging.LogRecord("t", logging.INFO, "", 0, "aaa", (), None)
        rec2 = logging.LogRecord("t", logging.INFO, "", 0, "aaa", (), None)
        rec3 = logging.LogRecord("t", logging.INFO, "", 0, "bbb", (), None)
        assert filt.filter(rec1) is True
        assert filt.filter(rec2) is False
        assert filt.filter(rec3) is True


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------


class TestSetupLogging:
    def test_creates_log_dir(self, tmp_path):
        log_dir = str(tmp_path / "new_logs")
        assert not os.path.exists(log_dir)

        setup_logging(log_dir=log_dir, rotate_seconds=9999)

        assert os.path.isdir(log_dir)
        # Очистить handler с таймером
        for h in logging.getLogger().handlers:
            h.close()

    def test_json_format(self, tmp_log_dir):
        setup_logging(log_dir=tmp_log_dir, json_format=True, rotate_seconds=9999)
        logger = logging.getLogger("test_json")
        logger.info("json_test_message")

        # Проверить, что файл содержит JSON
        for h in logging.getLogger().handlers:
            if isinstance(h, RotatingSessionFileHandler):
                with open(h.current_path, encoding="utf-8") as f:
                    content = f.read()
                assert "json_test_message" in content
                h.close()
                break

    def test_console_format(self, tmp_log_dir):
        setup_logging(log_dir=tmp_log_dir, json_format=False, rotate_seconds=9999)
        logger = logging.getLogger("test_console")
        logger.info("console_test_message")

        for h in logging.getLogger().handlers:
            if isinstance(h, RotatingSessionFileHandler):
                with open(h.current_path, encoding="utf-8") as f:
                    content = f.read()
                assert "console_test_message" in content
                h.close()
                break


# ---------------------------------------------------------------------------
# get_structlog
# ---------------------------------------------------------------------------


class TestGetStructlog:
    def test_returns_logger(self):
        setup_logging(rotate_seconds=9999)
        slog = get_structlog("test")
        assert slog is not None
        for h in logging.getLogger().handlers:
            h.close()
