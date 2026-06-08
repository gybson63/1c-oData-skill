#!/usr/bin/env python3
"""Журнал ответов бота, содержащих слово «Ошибка».

Позволяет собирать пользовательские ответы с ошибками для последующего разбора.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from html import unescape
from pathlib import Path
from typing import Any

from bot.logging_config import get_session_id
from bot.metrics import metrics

log = logging.getLogger(__name__)

_ERROR_WORD_RE = re.compile(r"ошибка", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")

_journal_dir: str | None = None
_journal_file: Path | None = None


def setup_error_response_journal(log_dir: str = "logs") -> None:
    """Инициализировать append-only журнал ``error_responses.jsonl``."""
    global _journal_dir, _journal_file
    _journal_dir = log_dir
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    _journal_file = path / "error_responses.jsonl"
    log.info("error_response_journal_initialized file=%s", _journal_file)


def plain_text_from_html(text: str) -> str:
    """Убрать HTML-теги для поиска по тексту ответа."""
    if not text:
        return ""
    no_tags = _HTML_TAG_RE.sub(" ", text)
    return unescape(re.sub(r"\s+", " ", no_tags)).strip()


def response_contains_error_word(text: str) -> bool:
    """Проверить, есть ли в ответе слово «Ошибка» (без учёта регистра)."""
    return bool(_ERROR_WORD_RE.search(plain_text_from_html(text)))


def journal_error_response_if_needed(
    *,
    answer: str,
    user_query: str = "",
    channel: str = "",
    chat_id: int | None = None,
    conversation_id: str | None = None,
    raw_answer: str | None = None,
    source: str = "outbound",
    extra: dict[str, Any] | None = None,
) -> bool:
    """Записать ответ в журнал, если в нём есть «Ошибка». Возвращает True, если записано."""
    if not response_contains_error_word(answer):
        return False

    plain = plain_text_from_html(answer)
    record: dict[str, Any] = {
        "ts": datetime.now(tz=UTC).isoformat(),
        "session_id": get_session_id(),
        "channel": channel,
        "chat_id": chat_id,
        "conversation_id": conversation_id,
        "source": source,
        "user_query": (user_query or "")[:2000],
        "answer_plain": plain[:4000],
        "answer_html": (answer or "")[:8000],
    }
    if raw_answer and raw_answer != answer:
        record["raw_answer"] = raw_answer[:4000]
    if extra:
        record["extra"] = extra

    metrics.increment("response_errors_logged")
    log.warning(
        "response_contains_error_word channel=%s chat_id=%s query=%r snippet=%r",
        channel,
        chat_id,
        (user_query or "")[:120],
        plain[:240],
    )
    _append_journal_record(record)
    return True


def _append_journal_record(record: dict[str, Any]) -> None:
    if _journal_file is None:
        log.debug("error_response_journal skipped: not initialized")
        return
    try:
        with open(_journal_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        log.info("error_response_journal_saved file=%s", _journal_file)
    except OSError as exc:
        log.warning("Failed to append error response journal: %s", exc)
