#!/usr/bin/env python3
"""Блокировка потенциально рискованных и высоконагруженных запросов.

Отклоняет полную выгрузку справочников без фильтра или лимита до обращения к AI/OData.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bot.agents.odata.request_brief_advisor import extract_current_query
from bot.config import get_settings

_BULK_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I)
    for p in (
        r"(?:выгруз\w*|экспорт\w*).*(?:\bвсе\b|\bвесь\b|\bполн\w*)",
        r"(?:\bвсе\b|\bвесь\b|\bвся\b)\s+(?:запис\w*|данн\w*|элемент\w*)",
        r"(?:\bвесь\b|\bвся\b)\s+справочник",
        r"полн\w*\s+(?:выгруз\w*|список|каталог|дамп|dump)",
        r"без\s+(?:фильтр\w*|огранич\w*)",
        r"(?:export\s+all|entire\s+(?:catalog|list))",
        r"(?:скачай|скачать|download)\s+(?:все\b|весь\b|полн\w*)",
    )
)

_BOUNDED_LIMIT = re.compile(
    r"(?:до|не\s+более|максимум|limit|top|перв(?:ые|ую|ых))\s*(\d+)",
    re.I,
)
_EXPLICIT_COUNT = re.compile(
    r"\b(\d+)\s+(?:запис\w*|сотрудник\w*|элемент\w*|строк\w*|чел\w*|контрагент\w*)",
    re.I,
)

_FILTERED_ALL = re.compile(
    r"(?:\bвсех?\b|\bкажд\w+\b).+(?:отдел|подраздел| где |табельн|принят|уволен|фамили)",
    re.I,
)


@dataclass(frozen=True)
class GuardLimits:
    """Лимиты запросов из ``odata`` в env.json / настройках приложения."""

    max_top: int
    default_top: int


def guard_limits_from_settings() -> GuardLimits:
    """Прочитать лимиты из ``settings.odata_query``."""
    odata = get_settings().odata_query
    return GuardLimits(max_top=odata.max_top, default_top=odata.default_top)


@dataclass(frozen=True)
class GuardResult:
    """Результат проверки запроса."""

    blocked: bool
    reason: str = ""

    def message_html(self, *, limits: GuardLimits) -> str:
        if self.reason.startswith("explicit_limit="):
            requested = self.reason.removeprefix("explicit_limit=").split(">", 1)[0]
            return (
                "⚠️ <b>Запрос отклонён:</b> указан лимит "
                f"({requested}) больше допустимого максимума ({limits.max_top}).<br><br>"
                f"Уменьшите число записей до {limits.max_top} или меньше, "
                "либо уточните условия отбора (подразделение, организация, период)."
            )
        return (
            "⚠️ <b>Запрос отклонён:</b> полная выгрузка без ограничений — "
            "высоконагруженная операция для базы 1С.<br><br>"
            "Уточните условия отбора (подразделение, организация, период) "
            f"или укажите лимит записей (например, «первые {limits.default_top}»)."
        )


def check_request_allowed(
    user_text: str,
    *,
    limits: GuardLimits | None = None,
) -> GuardResult:
    """Проверить, можно ли выполнять запрос пользователя.

    Args:
        user_text: полный текст запроса (в т.ч. email-контекст).
        limits: явные лимиты; если не переданы — читаются из настроек ``odata``.

    Returns:
        :class:`GuardResult` с ``blocked=True``, если запрос нужно отклонить.
    """
    limits = limits or guard_limits_from_settings()
    query = _normalize(extract_current_query(user_text))
    if not query:
        return GuardResult(blocked=False)

    explicit_limit = _extract_explicit_limit(query)
    if explicit_limit is not None:
        if explicit_limit > limits.max_top:
            return GuardResult(
                blocked=True,
                reason=f"explicit_limit={explicit_limit}>{limits.max_top}",
            )
        return GuardResult(blocked=False)

    if _FILTERED_ALL.search(query):
        return GuardResult(blocked=False)

    for pattern in _BULK_PATTERNS:
        if pattern.search(query):
            return GuardResult(blocked=True, reason=f"bulk_pattern={pattern.pattern[:40]}")

    return GuardResult(blocked=False)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _extract_explicit_limit(query: str) -> int | None:
    match = _BOUNDED_LIMIT.search(query)
    if match:
        return int(match.group(1))
    match = _EXPLICIT_COUNT.search(query)
    if match:
        return int(match.group(1))
    return None
