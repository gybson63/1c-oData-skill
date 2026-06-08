#!/usr/bin/env python3
"""Тесты журнала ответов со словом «Ошибка»."""

from __future__ import annotations

import json

from bot.response_error_journal import (
    journal_error_response_if_needed,
    plain_text_from_html,
    response_contains_error_word,
    setup_error_response_journal,
)


def test_plain_text_from_html_strips_tags():
    assert plain_text_from_html("<b>Ошибка</b> OData") == "Ошибка OData"


def test_response_contains_error_word_case_insensitive():
    assert response_contains_error_word("⚠️ ошибка разбора запроса")
    assert response_contains_error_word("<b>Ошибка AI</b>")
    assert not response_contains_error_word("Данные получены успешно")


def test_response_is_parse_failure_detected():
    from bot.agents.odata.parse_failure import response_is_parse_failure

    assert response_is_parse_failure("⚠️ Не удалось разобрать запрос. Попробуйте переформулировать.")
    assert not response_is_parse_failure("10 сотрудников в таблице")


def test_journal_appends_jsonl(tmp_path):
    setup_error_response_journal(str(tmp_path))
    logged = journal_error_response_if_needed(
        answer="❌ <b>Ошибка OData:</b> entity not found",
        user_query="Покажи справочник",
        channel="email",
        chat_id=42,
        conversation_id="conv-1",
    )
    assert logged is True

    lines = (tmp_path / "error_responses.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["channel"] == "email"
    assert record["chat_id"] == 42
    assert "Ошибка OData" in record["answer_plain"]
    assert record["user_query"] == "Покажи справочник"


def test_journal_skips_clean_response(tmp_path):
    setup_error_response_journal(str(tmp_path))
    logged = journal_error_response_if_needed(
        answer="<b>📋 Список сотрудников</b>",
        user_query="Покажи сотрудников",
        channel="telegram",
    )
    assert logged is False
    assert not (tmp_path / "error_responses.jsonl").exists()
