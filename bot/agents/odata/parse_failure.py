#!/usr/bin/env python3
"""Классификация и журналирование ошибок разбора Step 1 (AI → OData JSON)."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from bot.logging_config import get_session_id
from bot.metrics import metrics
from bot.response_error_journal import plain_text_from_html

log = logging.getLogger(__name__)

_PARSE_FAILURE_RE = re.compile(
    r"разобрать\s+запрос|переформулировать",
    re.IGNORECASE,
)

_journal_dir: str | None = None
_journal_file: Path | None = None


class ParseFailureReason(StrEnum):
    """Причина неудачного разбора ответа AI Step 1."""

    JSON_NOT_FOUND = "json_not_found"
    INLINE_TOOL_SHAPE = "inline_tool_shape"
    EMPTY_ENTITY = "empty_entity"
    ANALYTICS_INVALID = "analytics_invalid"
    TOOL_CHAIN_EXHAUSTED = "tool_chain_exhausted"
    AUTO_SEARCH_RETRY_FAILED = "auto_search_retry_failed"


def setup_parse_failure_journal(log_dir: str = "logs") -> None:
    """Инициализировать append-only журнал ``parse_failures.jsonl``."""
    global _journal_dir, _journal_file
    _journal_dir = log_dir
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    _journal_file = path / "parse_failures.jsonl"
    log.info("parse_failure_journal_initialized file=%s", _journal_file)


def response_is_parse_failure(text: str) -> bool:
    """Проверить, что ответ пользователю — ошибка разбора Step 1."""
    plain = plain_text_from_html(text)
    return bool(_PARSE_FAILURE_RE.search(plain))


def classify_step1_failure(
    ai_response: str,
    *,
    query_dict: dict[str, Any] | None = None,
    apply_failed_reason: str | None = None,
) -> ParseFailureReason:
    """Определить причину неудачного разбора ответа AI."""
    if apply_failed_reason == ParseFailureReason.ANALYTICS_INVALID:
        return ParseFailureReason.ANALYTICS_INVALID

    if query_dict is None:
        return ParseFailureReason.JSON_NOT_FOUND

    if query_dict.get("mode") == "analytics":
        return ParseFailureReason.ANALYTICS_INVALID

    from bot.agents.odata.query_parser import is_inline_tool_call

    if is_inline_tool_call(query_dict):
        return ParseFailureReason.INLINE_TOOL_SHAPE

    entity = query_dict.get("entity")
    if not entity:
        return ParseFailureReason.EMPTY_ENTITY

    return ParseFailureReason.TOOL_CHAIN_EXHAUSTED


def build_parse_failure_message(ai_response: str, *, snippet_len: int = 500) -> str:
    """Сформировать HTML-сообщение для пользователя."""
    from bot.utils import esc_html

    snippet = esc_html((ai_response or "")[:snippet_len])
    return f"Не удалось разобрать запрос. Попробуйте переформулировать.\n\n<pre>{snippet}</pre>"


def find_latest_step1_artifact(log_dir: str = "logs") -> str | None:
    """Найти последний сохранённый ``NNN_step1.json`` для текущей session."""
    session_id = get_session_id()
    if not session_id:
        return None
    response_dir = Path(log_dir) / session_id
    if not response_dir.is_dir():
        return None
    candidates = sorted(response_dir.glob("*_step1.json"))
    if not candidates:
        return None
    return str(candidates[-1])


def journal_parse_failure(
    *,
    user_query: str,
    ai_response: str,
    reason: ParseFailureReason | str,
    channel: str = "",
    chat_id: int | None = None,
    conversation_id: str | None = None,
    step1_artifact: str | None = None,
    extra: dict[str, Any] | None = None,
) -> bool:
    """Записать parse failure в ``parse_failures.jsonl``."""
    reason_str = str(reason)
    metrics.increment("odata_step1_parse_failures")
    metrics.increment(f"odata_step1_parse_failures_{reason_str}")

    snippet = (ai_response or "")[:2000]
    record: dict[str, Any] = {
        "ts": datetime.now(tz=UTC).isoformat(),
        "session_id": get_session_id(),
        "channel": channel,
        "chat_id": chat_id,
        "conversation_id": conversation_id,
        "failure_reason": reason_str,
        "user_query": (user_query or "")[:2000],
        "ai_response_snippet": snippet,
        "step1_artifact": step1_artifact or find_latest_step1_artifact(_journal_dir or "logs"),
    }
    if extra:
        record["extra"] = extra

    log.warning(
        "step1_parse_failure reason=%s channel=%s chat_id=%s query=%r snippet=%r",
        reason_str,
        channel,
        chat_id,
        (user_query or "")[:120],
        snippet[:240],
    )
    _append_journal_record(record)
    return True


def journal_parse_failure_from_response(
    *,
    answer: str,
    user_query: str = "",
    channel: str = "",
    chat_id: int | None = None,
    conversation_id: str | None = None,
    raw_answer: str | None = None,
    source: str = "outbound",
) -> bool:
    """Записать в журнал, если outbound-ответ — parse failure."""
    if not response_is_parse_failure(answer):
        return False
    journal_parse_failure(
        user_query=user_query,
        ai_response=raw_answer or plain_text_from_html(answer),
        reason=ParseFailureReason.TOOL_CHAIN_EXHAUSTED,
        channel=channel,
        chat_id=chat_id,
        conversation_id=conversation_id,
        extra={"source": source, "detected_from_outbound": True},
    )
    return True


def _append_journal_record(record: dict[str, Any]) -> None:
    if _journal_file is None:
        log.debug("parse_failure_journal skipped: not initialized")
        return
    try:
        with open(_journal_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        log.info("parse_failure_journal_saved file=%s", _journal_file)
    except OSError as exc:
        log.warning("Failed to append parse failure journal: %s", exc)


def load_parse_failures(log_dir: str = "logs") -> list[dict[str, Any]]:
    """Прочитать все записи из ``parse_failures.jsonl``."""
    path = Path(log_dir) / "parse_failures.jsonl"
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records
