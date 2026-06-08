#!/usr/bin/env python3
"""Тесты keyword extraction (fallback path аналитика)."""

from bot.agents.odata.conf_doc_context import build_conf_doc_search_queries


def test_vacation_balance_keywords():
    queries = build_conf_doc_search_queries("Сколько дней отпуска осталось у сотрудников?")
    assert any("АналитикаОстатковОтпусков" in q for q in queries)
    assert any("остатки" in q.lower() or "остаток" in q.lower() for q in queries)


def test_employee_keywords():
    queries = build_conf_doc_search_queries("Покажи 10 штатных сотрудников: ФИО, должность")
    assert "КадроваяИсторияСотрудников" in queries or "Сотрудники" in queries


def test_salary_keywords():
    queries = build_conf_doc_search_queries("Покажи начисления зарплаты за апрель 2025")
    assert any("Начисл" in q or "зарплат" in q.lower() for q in queries)
