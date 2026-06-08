#!/usr/bin/env python3
"""Анализ CSV-вложений без обращения к OData."""

from __future__ import annotations

import csv
import io
import logging
import re
from typing import Any

from bot.agents.odata.state import ODataState

log = logging.getLogger(__name__)

_ATTACHMENTS_MARKER = re.compile(r"---\s*Вложения\s*---", re.I)
_ANALYZE_INTENT = re.compile(
    r"проанализируй|анализ\s|разбор|сравни|сводк|построй.*по\s+данным|по\s+вложен",
    re.I,
)
_CSV_BLOCK = re.compile(
    r"---\s*(?P<name>[^\n]+?\.csv)\s*---\s*\n(?P<body>.*?)(?=\n---\s|\Z)",
    re.I | re.S,
)


def is_csv_analysis_request(user_text: str) -> bool:
    """Запрос на анализ CSV-вложения (не OData)."""
    if not _ATTACHMENTS_MARKER.search(user_text):
        return False
    if ".csv" not in user_text.lower():
        return False
    return bool(_ANALYZE_INTENT.search(user_text))


def extract_csv_tables(user_text: str) -> list[tuple[str, list[dict[str, Any]]]]:
    """Извлечь таблицы из текста email с вложениями."""
    tables: list[tuple[str, list[dict[str, Any]]]] = []
    for match in _CSV_BLOCK.finditer(user_text):
        name = match.group("name").strip()
        body = match.group("body").strip()
        if not body:
            continue
        try:
            reader = csv.DictReader(io.StringIO(body))
            rows = list(reader)
            if rows:
                tables.append((name, rows))
        except csv.Error as exc:
            log.warning("CSV parse error for %s: %s", name, exc)
    return tables


def _records_to_html_table(name: str, rows: list[dict[str, Any]], max_rows: int = 50) -> str:
    if not rows:
        return f"<i>{name}: пустой файл</i>"
    cols = list(rows[0].keys())
    lines = [f"<b>{name}</b>", "<table>"]
    lines.append("<tr>" + "".join(f"<th>{c}</th>" for c in cols) + "</tr>")
    for row in rows[:max_rows]:
        lines.append("<tr>" + "".join(f"<td>{row.get(c, '')}</td>" for c in cols) + "</tr>")
    lines.append("</table>")
    if len(rows) > max_rows:
        lines.append(f"<i>… ещё {len(rows) - max_rows} строк</i>")
    return "\n".join(lines)


async def try_handle_csv_attachment(state: ODataState, ai_service: Any) -> ODataState | None:
    """Обработать CSV-вложение: таблица + краткий AI-комментарий."""
    if not is_csv_analysis_request(state.user_text):
        return None

    tables = extract_csv_tables(state.user_text)
    if not tables:
        state.answer_html = (
            "⚠️ <b>Не удалось прочитать CSV из вложения.</b> Проверьте формат файла (заголовок + строки данных)."
        )
        state.history = state.finalize_history(
            10,
            assistant_content=state.answer_html,
        )
        return state

    html_parts = [_records_to_html_table(name, rows) for name, rows in tables]
    all_rows: list[dict[str, Any]] = []
    for _, rows in tables:
        all_rows.extend(rows)

    summary_lines = [f"<b>Данные из вложения</b> ({len(all_rows)} строк)"]
    summary_lines.extend(html_parts)

    try:
        ai_summary = await ai_service.step2_format_response(
            user_text=state.user_text,
            records=all_rows[:30],
            total=len(all_rows),
            entity="CSV_вложение",
            shown=min(len(all_rows), 30),
            chat_id=state.chat_id,
        )
        summary_lines.append(ai_summary)
    except Exception as exc:
        log.warning("CSV Step2 failed, table-only response: %s", exc)

    state.answer_html = "\n\n".join(summary_lines)
    state.history = state.finalize_history(
        10,
        assistant_content=state.answer_html,
    )
    log.info("CSV attachment handled: %d tables, %d rows", len(tables), len(all_rows))
    return state
