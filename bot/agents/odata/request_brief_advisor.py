#!/usr/bin/env python3
"""Субагент краткой формулировки запроса для заголовка ответа.

Формирует headline, соотнесённый с вопросом пользователя (в т.ч. follow-up в треде).
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from bot.agents.odata.analytics_models import RequestBrief
from bot.agents.odata.prompts import REQUEST_BRIEF_SYSTEM
from bot.agents.odata.tool_resolver import _extract_json

if TYPE_CHECKING:
    from bot.agents.odata.ai_service import AIService

log = logging.getLogger(__name__)

_EMAIL_CURRENT_MARKER = "--- Текущий запрос ---"
_MAX_RULES_HEADLINE = 90
_MIN_FOLLOWUP_LEN = 28

_CHART_HINTS = ("график", "диаграмм", "chart", "визуал", "динамик", "тренд")
_COUNT_HINTS = ("сколько", "количеств", "численност", "count", "итого")
_LIST_HINTS = ("список", "списком", "покажи", "выведи", "перечень", "таблиц")


class RequestBriefAdvisor:
    """Создаёт краткую формулировку запроса для заголовка ответа."""

    def __init__(self, *, max_headline_len: int = 90) -> None:
        self._max_headline_len = max_headline_len

    async def advise(
        self,
        ai: AIService | None,
        *,
        user_query: str,
        history: list[dict[str, str]] | None = None,
        chat_id: int | None = None,
    ) -> RequestBrief:
        """Получить краткий заголовок, соответствующий запросу пользователя."""
        current = extract_current_query(user_query)
        history = history or []

        if _needs_ai_brief(current, history):
            if ai is not None:
                ai_brief = await self._ai_brief(ai, current=current, history=history, chat_id=chat_id)
                if ai_brief is not None:
                    return ai_brief
            contextual = _brief_from_history(current, history)
            if contextual is not None:
                return contextual

        return brief_from_rules(current)

    async def _ai_brief(
        self,
        ai: AIService,
        *,
        current: str,
        history: list[dict[str, str]],
        chat_id: int | None,
    ) -> RequestBrief | None:
        context_lines = []
        for msg in history[-4:]:
            role = msg.get("role", "user")
            content = (msg.get("content") or "").strip()
            if not content or len(content) > 400:
                continue
            label = "Пользователь" if role == "user" else "Бот"
            context_lines.append(f"{label}: {content}")

        payload = {
            "current_query": current,
            "dialog_context": context_lines or None,
        }
        messages = [
            {"role": "system", "content": REQUEST_BRIEF_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
        ]
        try:
            content = await ai.request_brief(messages, chat_id=chat_id)
        except Exception as e:
            log.warning("RequestBriefAdvisor AI failed: %s", e)
            return None

        parsed = _extract_json(content)
        if not parsed:
            log.warning("RequestBriefAdvisor: не удалось разобрать JSON: %s", content[:300])
            return None

        headline = str(parsed.get("headline") or "").strip()
        if not headline:
            return None
        emoji = str(parsed.get("emoji") or _pick_emoji(headline)).strip() or _pick_emoji(headline)
        return RequestBrief(
            headline=_normalize_headline(headline, self._max_headline_len),
            emoji=emoji[:4] if emoji else _pick_emoji(headline),
            source="ai",
        )


def extract_current_query(user_text: str) -> str:
    """Выделить текущий вопрос из полного текста (email-контекст, тред)."""
    text = (user_text or "").strip()
    if not text:
        return "Запрос к данным 1С"

    if _EMAIL_CURRENT_MARKER in text:
        part = text.split(_EMAIL_CURRENT_MARKER, 1)[-1].strip()
        if part:
            return part

    if "Контекст email-переписки" in text and "Текущий запрос" in text:
        match = re.search(r"---\s*Текущий запрос\s*---\s*(.+)", text, re.S | re.I)
        if match:
            return match.group(1).strip()

    return text


def brief_from_rules(query: str) -> RequestBrief:
    """Правила без AI: нормализация и усечение запроса."""
    cleaned = _normalize_headline(query, _MAX_RULES_HEADLINE)
    if not cleaned:
        cleaned = "Запрос к данным 1С"
    return RequestBrief(headline=cleaned, emoji=_pick_emoji(cleaned), source="rules")


def _needs_ai_brief(current: str, history: list[dict[str, str]]) -> bool:
    compact = re.sub(r"\s+", " ", current).strip()
    if len(compact) > _MAX_RULES_HEADLINE:
        return True
    if len(compact) < _MIN_FOLLOWUP_LEN and history:
        return True
    if compact.endswith("?") and len(compact.split()) <= 3 and history:
        return True
    return False


def _brief_from_history(current: str, history: list[dict[str, str]]) -> RequestBrief | None:
    last_user = ""
    for msg in reversed(history):
        if msg.get("role") == "user":
            last_user = (msg.get("content") or "").strip()
            if last_user:
                break
    if not last_user:
        return None
    headline = f"{current} ({_truncate_words(last_user, 8)})"
    headline = _normalize_headline(headline, _MAX_RULES_HEADLINE)
    return RequestBrief(headline=headline, emoji=_pick_emoji(headline), source="rules")


def _normalize_headline(text: str, max_len: int) -> str:
    line = re.sub(r"\s+", " ", (text or "").strip())
    line = line.strip(" \"'«»")
    if len(line) <= max_len:
        return line
    cut = line[: max_len - 1].rsplit(" ", 1)[0]
    return (cut or line[: max_len - 1]) + "…"


def _truncate_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "…"


def _pick_emoji(text: str) -> str:
    lower = text.lower()
    if any(h in lower for h in _CHART_HINTS):
        return "📊"
    if any(h in lower for h in _COUNT_HINTS):
        return "🔢"
    if any(h in lower for h in _LIST_HINTS):
        return "📋"
    return "📋"
