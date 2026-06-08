#!/usr/bin/env python3
"""Обогащение Step 1 промпта контекстом из conf-doc."""

from __future__ import annotations

import logging
import re
from typing import Any

from bot.config import ConfDocSettings
from bot_lib.conf_doc_client import ConfDocApiError, ConfDocClient

log = logging.getLogger(__name__)

OBJECT_TYPE_TO_ODATA: dict[str, str] = {
    "Catalog": "Catalog_",
    "Document": "Document_",
    "DocumentJournal": "DocumentJournal_",
    "Enum": "Enum_",
    "InformationRegister": "InformationRegister_",
    "AccumulationRegister": "AccumulationRegister_",
    "CalculationRegister": "CalculationRegister_",
    "AccountingRegister": "AccountingRegister_",
    "ChartOfAccounts": "ChartOfAccounts_",
    "ChartOfCalculationTypes": "ChartOfCalculationTypes_",
    "ChartOfCharacteristicTypes": "ChartOfCharacteristicTypes_",
    "ExchangePlan": "ExchangePlan_",
    "Constant": "Constant_",
    "BusinessProcess": "BusinessProcess_",
    "Task": "Task_",
}

_ODATA_OBJECT_TYPES: frozenset[str] = frozenset(
    {
        "Catalog",
        "Document",
        "DocumentJournal",
        "InformationRegister",
        "AccumulationRegister",
        "CalculationRegister",
        "AccountingRegister",
        "Enum",
    }
)

_LOW_PRIORITY_TYPES: frozenset[str] = frozenset(
    {
        "Role",
        "Report",
        "CommonForm",
        "Constant",
        "Subsystem",
        "CommonCommand",
        "CommonModule",
    }
)

_STOP_WORDS: frozenset[str] = frozenset(
    {
        "покажи",
        "показать",
        "выведи",
        "список",
        "сколько",
        "дай",
        "все",
        "всех",
        "за",
        "по",
        "и",
        "в",
        "на",
        "не",
        "какие",
        "какой",
        "какая",
        "что",
        "где",
        "когда",
        "было",
        "были",
        "есть",
        "мне",
        "нужно",
        "хочу",
        "пожалуйста",
    }
)

_EMPLOYEE_RE = re.compile(r"сотрудник|работник|штатн|фио|должност|подраздел", re.I)
_VACATION_BALANCE_RE = re.compile(
    r"остал|остаток|остатк|дней\s+отпуск|отпуск.*остал",
    re.I,
)
_VACATION_BALANCE_CONF_QUERIES: tuple[str, ...] = (
    "АналитикаОстатковОтпусков",
    "ФактическиеОтпуска",
    "НачальныеОстаткиОтпусков",
    "остатки отпусков",
    "ОстаткиОтпусков",
)
_VACATION_BALANCE_REPORT: tuple[str, str] = ("Report", "ОстаткиОтпусков")
_SALARY_RE = re.compile(r"начисл|зарплат|ведомост|удержан", re.I)
_SALARY_CONF_QUERIES: tuple[str, ...] = (
    "НачислениеЗарплаты",
    "начисление зарплаты",
    "ВедомостьНаВыплатуЗарплатыВБанк",
    "АнализНачислений",
)
_DOMAIN_REPORT_HINTS: tuple[tuple[re.Pattern[str], tuple[str, str]], ...] = (
    (_VACATION_BALANCE_RE, _VACATION_BALANCE_REPORT),
)
_REGISTER_NAME_RE = re.compile(
    r"(?:Регистр(?:Сведений|Накопления|Расчета)?\.|"
    r"InformationRegister\.|AccumulationRegister\.|CalculationRegister\.)(\w+)"
)
_QUERY_BODY_RE = re.compile(
    r"Регистр(?:Сведений|Накопления|Расчета)?\.|"
    r"InformationRegister\.|AccumulationRegister\.|CalculationRegister\.|"
    r"\bВЫБРАТЬ\b|\bИЗ\b|\bСОЕДИНЕНИЕ\b|\bОБЪЕДИНИТЬ\b",
    re.I,
)
_SKD_HEADER_RE = re.compile(r"^##\s+Запрос\s+СКД", re.I | re.M)


def build_conf_doc_search_queries(user_query: str, request_brief: str | None = None) -> list[str]:
    """Сформировать короткие поисковые запросы для conf-doc."""
    queries: list[str] = []

    brief = (request_brief or "").strip()
    if brief and len(brief) >= 8:
        queries.append(brief)

    keywords = _extract_keywords(user_query)
    if keywords and keywords not in queries:
        queries.append(keywords)

    if _EMPLOYEE_RE.search(user_query):
        for extra in ("КадроваяИсторияСотрудников", "Сотрудники"):
            if extra not in queries:
                queries.append(extra)

    if _VACATION_BALANCE_RE.search(user_query):
        for extra in _VACATION_BALANCE_CONF_QUERIES:
            if extra not in queries:
                queries.append(extra)

    if _SALARY_RE.search(user_query):
        for extra in _SALARY_CONF_QUERIES:
            if extra not in queries:
                queries.append(extra)

    if not queries:
        cleaned = re.sub(r"\s+", " ", user_query).strip()
        if cleaned:
            queries.append(cleaned[:120])

    return queries


def _extract_keywords(text: str) -> str:
    cleaned = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    words = [w for w in cleaned.split() if len(w) > 2 and w.lower() not in _STOP_WORDS]
    return " ".join(words[:8])


def filter_search_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Оставить объекты, релевантные для OData."""
    if not results:
        return []

    preferred = [r for r in results if r.get("object_type") in _ODATA_OBJECT_TYPES]
    if preferred:
        return preferred

    fallback = [r for r in results if r.get("object_type") not in _LOW_PRIORITY_TYPES]
    return fallback or results


def merge_search_results(
    batches: list[list[dict[str, Any]]],
    top_k: int,
) -> list[dict[str, Any]]:
    """Объединить результаты нескольких поисков, отсортировать по score."""
    seen: set[tuple[str, str]] = set()
    merged: list[dict[str, Any]] = []

    for batch in batches:
        for item in filter_search_results(batch):
            key = (str(item.get("object_type", "")), str(item.get("name", "")))
            if not key[1] or key in seen:
                continue
            seen.add(key)
            merged.append(item)

    merged.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
    return merged[:top_k]


def _odata_entity_name(object_type: str, name: str) -> str:
    prefix = OBJECT_TYPE_TO_ODATA.get(object_type, "")
    if prefix:
        return f"{prefix}{name}"
    return ""


def format_search_results(results: list[dict[str, Any]]) -> str:
    """Форматировать результаты search для промпта."""
    if not results:
        return ""

    lines: list[str] = []
    for item in results:
        object_type = str(item.get("object_type", ""))
        name = str(item.get("name", ""))
        if not name:
            continue
        score = item.get("score")
        score_part = f" (score={score:.2f})" if isinstance(score, (int, float)) else ""
        lines.append(f"- {object_type}.{name}{score_part}")
        odata = _odata_entity_name(object_type, name)
        if odata:
            lines.append(f"  OData: {odata}")
        text = str(item.get("text") or "").strip()
        if text:
            snippet = text[:400] + ("..." if len(text) > 400 else "")
            lines.append(f"  {snippet}")

    return "\n".join(lines)


def collect_report_drilldown_targets(
    user_query: str,
    batches: list[list[dict[str, Any]]],
) -> list[tuple[str, str]]:
    """Собрать Report-объекты для drill-down СКД."""
    targets: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for pattern, report in _DOMAIN_REPORT_HINTS:
        if pattern.search(user_query) and report not in seen:
            targets.append(report)
            seen.add(report)

    for batch in batches:
        for item in batch:
            if item.get("object_type") != "Report":
                continue
            key = ("Report", str(item.get("name", "")))
            if key[1] and key not in seen:
                targets.append(key)
                seen.add(key)

    return targets


def select_skd_related_chunks(chunks: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Выбрать чанки с СКД и их продолжения."""
    if not chunks:
        return []

    selected: list[tuple[int, str]] = []
    include_next = False

    for idx, text in chunks:
        if _SKD_HEADER_RE.search(text) or "## Модуль объекта" in text:
            selected.append((idx, text))
            include_next = True
            continue
        if include_next and _QUERY_BODY_RE.search(text):
            selected.append((idx, text))
            continue
        if include_next and not text.strip().startswith("#"):
            selected.append((idx, text))
            continue
        include_next = False

    return selected


def format_report_skd_block(
    object_type: str,
    name: str,
    chunks: list[tuple[int, str]],
) -> str:
    """Форматировать блок СКД отчёта."""
    lines = [f"Report drill-down: {object_type}.{name}"]
    combined = "\n".join(text for _, text in chunks)
    registers = sorted(set(_REGISTER_NAME_RE.findall(combined)))
    if registers:
        lines.append("Регистры из запроса СКД: " + ", ".join(registers))
    for _, text in chunks:
        if "Запрос СКД" in text or _QUERY_BODY_RE.search(text):
            snippet = text.strip()[:600]
            lines.append(snippet)
    return "\n".join(lines)


async def fetch_report_skd_context(
    client: ConfDocClient,
    object_type: str,
    name: str,
) -> str:
    """Загрузить чанки СКД отчёта."""
    try:
        obj = await client.get_object(object_type, name)
    except ConfDocApiError as exc:
        log.debug("conf-doc get_object failed for %s.%s: %s", object_type, name, exc)
        return ""

    chunk_meta = obj.get("chunks") or []
    chunks: list[tuple[int, str]] = []
    for meta in chunk_meta:
        idx = meta.get("chunk_index", meta.get("index", 0))
        try:
            chunk = await client.get_object_chunk(object_type, name, int(idx))
        except ConfDocApiError:
            continue
        text = str(chunk.get("text") or "")
        chunks.append((int(idx), text))

    selected = select_skd_related_chunks(chunks)
    if not selected:
        return ""
    return format_report_skd_block(object_type, name, selected)


async def fetch_conf_doc_context(
    user_query: str,
    settings: ConfDocSettings,
    *,
    request_brief: str | None = None,
) -> str:
    """Получить текстовый блок conf-doc для промпта Step 1."""
    if not settings.enabled or not settings.enrich_prompt:
        return ""

    if not settings.api_url:
        return ""

    client = ConfDocClient(
        api_url=settings.api_url,
        configuration=settings.configuration,
        timeout=settings.timeout,
    )

    try:
        await client.health()
    except ConfDocApiError as exc:
        log.info("conf-doc unavailable: %s", exc)
        return ""

    queries = build_conf_doc_search_queries(user_query, request_brief=request_brief)
    batches: list[list[dict[str, Any]]] = []

    for query in queries:
        try:
            batch = await client.search(query, top_k=settings.search_top_k)
        except ConfDocApiError as exc:
            log.debug("conf-doc search failed for %r: %s", query, exc)
            continue
        if batch:
            batches.append(batch)

    merged = merge_search_results(batches, settings.search_top_k)
    parts: list[str] = []
    search_block = format_search_results(merged)
    if search_block:
        parts.append(search_block)

    report_targets = collect_report_drilldown_targets(user_query, batches)
    for object_type, name in report_targets[:3]:
        skd_block = await fetch_report_skd_context(client, object_type, name)
        if skd_block:
            parts.append(skd_block)

    if not parts:
        return ""

    log.info("conf_doc: enriched prompt with %d result groups", len(parts))
    return "\n\n".join(parts)
