"""Тесты блокировки высоконагруженных запросов."""

from __future__ import annotations

import pytest

from bot.agents.odata.request_guard import GuardLimits, check_request_allowed

_LIMITS = GuardLimits(max_top=50, default_top=20)


@pytest.mark.parametrize(
    "query",
    [
        "Выгрузи все записи справочника сотрудников",
        "выгрузить все данные по контрагентам",
        "полная выгрузка справочника организаций",
        "покажи все записи без фильтра",
        "export all employees",
        "скачай весь справочник номенклатуры",
        "выгрузи полный список сотрудников",
    ],
)
def test_blocks_bulk_export_without_limit(query: str):
    result = check_request_allowed(query, limits=_LIMITS)
    assert result.blocked is True
    assert "отклонён" in result.message_html(limits=_LIMITS)


@pytest.mark.parametrize(
    "query",
    [
        "📋 Выгрузи до 50 сотрудников с табельным номером и датой приёма",
        "Выгрузи до 50 сотрудников с табельным номером",
        "покажи первые 20 записей справочника",
        "не более 30 сотрудников подразделения продаж",
        "5 сотрудников с расшифровкой подразделения",
        "Покажи список работающих сотрудников с должностью",
        "Сколько сотрудников в организации?",
        "покажи всех сотрудников отдела бухгалтерии",
    ],
)
def test_allows_bounded_or_filtered_queries(query: str):
    result = check_request_allowed(query, limits=_LIMITS)
    assert result.blocked is False


def test_blocks_explicit_limit_above_max():
    result = check_request_allowed("выгрузи 500 сотрудников", limits=_LIMITS)
    assert result.blocked is True
    assert result.reason.startswith("explicit_limit=")
    msg = result.message_html(limits=_LIMITS)
    assert "500" in msg
    assert "50" in msg


def test_allows_limit_at_configured_max_top():
    """Лимит «до 50» допустим при max_top=50 из настроек."""
    query = "📋 Выгрузи до 50 сотрудников с табельным номером и датой приёма"
    assert check_request_allowed(query, limits=_LIMITS).blocked is False
    assert check_request_allowed(query, limits=GuardLimits(max_top=20, default_top=20)).blocked is True


def test_rejection_hint_uses_default_top_from_settings():
    result = check_request_allowed("выгрузи все сотрудники", limits=_LIMITS)
    msg = result.message_html(limits=_LIMITS)
    assert "первые 20" in msg


def test_extracts_current_query_from_email_context():
    text = (
        "Контекст email-переписки (3 сообщений):\n\n"
        "старое письмо\n\n"
        "--- Текущий запрос ---\n"
        "Выгрузи все записи справочника сотрудников"
    )
    result = check_request_allowed(text, limits=_LIMITS)
    assert result.blocked is True


def test_blocked_message_includes_request_headline():
    from bot.agents.odata.request_brief_advisor import brief_from_rules, extract_current_query
    from bot.agents.odata.response_headline import apply_request_headline

    query = "Выгрузи все записи справочника сотрудников"
    guard = check_request_allowed(query, limits=_LIMITS)
    assert guard.blocked is True
    brief = brief_from_rules(extract_current_query(query))
    answer = apply_request_headline(guard.message_html(limits=_LIMITS), brief)
    assert "выгруз" in answer.lower()
    assert "сотрудник" in answer.lower()
    assert "отклонён" in answer.lower()


def test_reads_limits_from_settings(monkeypatch):
    from bot.agents.odata import request_guard as guard_mod
    from bot.config import AppSettings, ODataQuerySettings

    settings = AppSettings(
        odata_query=ODataQuerySettings(max_top=100, default_top=15),
    )
    monkeypatch.setattr(guard_mod, "get_settings", lambda: settings)

    assert check_request_allowed("выгрузи до 100 сотрудников").blocked is False
    assert check_request_allowed("выгрузи до 101 сотрудников").blocked is True
