#!/usr/bin/env python3
"""Форматирование ответов для email (HTML с таблицами, списками, стилями)."""

from __future__ import annotations

import html
import logging
import re

log = logging.getLogger(__name__)

_EMAIL_WRAPPER = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         font-size: 14px; line-height: 1.5; color: #333; max-width: 800px; }}
  h2, h3 {{ color: #1a5276; margin-top: 1.2em; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
  th {{ background: #2e86c1; color: white; padding: 8px 12px; text-align: left; }}
  td {{ border: 1px solid #ddd; padding: 8px 12px; }}
  tr:nth-child(even) {{ background: #f8f9fa; }}
  ul, ol {{ margin: 8px 0; padding-left: 24px; }}
  li {{ margin: 4px 0; }}
  code {{ background: #f4f4f4; padding: 2px 6px; border-radius: 3px; font-size: 13px; }}
  pre {{ background: #f4f4f4; padding: 12px; border-radius: 4px; overflow-x: auto; }}
  .footer {{ margin-top: 24px; padding-top: 12px; border-top: 1px solid #eee;
             font-size: 12px; color: #888; }}
  .highlight {{ background: #fff3cd; padding: 2px 4px; }}
  .error {{ color: #c0392b; }}
  .success {{ color: #27ae60; }}
</style>
</head>
<body>
{body}
{footer}
</body>
</html>"""


def markdownish_to_html(text: str) -> str:
    """Конвертировать упрощённый markdown/HTML в email-safe HTML."""
    if not text:
        return ""

    # Если уже содержит HTML-теги — санитизируем и возвращаем
    if re.search(r"<(table|tr|td|th|ul|ol|li|h[1-6]|p|div|br)\b", text, re.IGNORECASE):
        return _sanitize_html(text)

    result = html.escape(text)

    # Заголовки
    result = re.sub(r"^### (.+)$", r"<h3>\1</h3>", result, flags=re.MULTILINE)
    result = re.sub(r"^## (.+)$", r"<h2>\1</h2>", result, flags=re.MULTILINE)

    # Жирный/курсив
    result = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", result)
    result = re.sub(r"_(.+?)_", r"<em>\1</em>", result)

    # Списки
    result = _convert_bullet_lists(result)

    # Таблицы (markdown-style | col | col |)
    result = _convert_markdown_tables(result)

    # Переносы строк → параграфы
    paragraphs = result.split("\n\n")
    html_parts = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if p.startswith("<"):
            html_parts.append(p)
        else:
            html_parts.append(f"<p>{p.replace(chr(10), '<br>')}</p>")

    return "\n".join(html_parts)


def _convert_bullet_lists(text: str) -> str:
    lines = text.split("\n")
    result: list[str] = []
    in_list = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("• ") or stripped.startswith("- "):
            if not in_list:
                result.append("<ul>")
                in_list = True
            result.append(f"<li>{stripped[2:]}</li>")
        else:
            if in_list:
                result.append("</ul>")
                in_list = False
            result.append(line)

    if in_list:
        result.append("</ul>")
    return "\n".join(result)


def _convert_markdown_tables(text: str) -> str:
    """Конвертировать markdown-таблицы (| a | b |) в HTML."""
    lines = text.split("\n")
    result: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        if "|" in line and i + 1 < len(lines) and re.match(r"^[\s|:-]+$", lines[i + 1].replace("|", "").strip() + "|"):
            # Это таблица
            header_cells = [c.strip() for c in line.split("|") if c.strip()]
            i += 2  # пропустить separator
            rows: list[list[str]] = [header_cells]
            while i < len(lines) and "|" in lines[i]:
                cells = [c.strip() for c in lines[i].split("|") if c.strip()]
                if cells:
                    rows.append(cells)
                i += 1

            table_html = _build_table(rows)
            result.append(table_html)
        else:
            result.append(line)
            i += 1

    return "\n".join(result)


def _build_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    html_parts = ["<table>"]
    html_parts.append("<tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in rows[0]) + "</tr>")
    for row in rows[1:]:
        html_parts.append("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in row) + "</tr>")
    html_parts.append("</table>")
    return "\n".join(html_parts)


def _sanitize_html(text: str) -> str:
    """Удалить опасные теги, оставить безопасное форматирование."""
    # Удалить script/style
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Удалить опасные атрибуты
    text = re.sub(r"\s(on\w+|javascript:)[^>]*", "", text, flags=re.IGNORECASE)
    return text


def format_email_reply(
    body_html: str,
    *,
    token_footer: str = "",
    original_subject: str = "",
) -> tuple[str, str]:
    """Подготовить HTML-ответ и plain text fallback.

    Returns:
        (html_body, plain_text)
    """
    content = markdownish_to_html(body_html)

    footer = ""
    if token_footer:
        footer = f'<div class="footer">{html.escape(token_footer)}</div>'

    full_html = _EMAIL_WRAPPER.format(body=content, footer=footer)

    # Plain text fallback
    plain = re.sub(r"<br\s*/?>", "\n", body_html, flags=re.IGNORECASE)
    plain = re.sub(r"<[^>]+>", "", plain)
    plain = html.unescape(plain)
    if token_footer:
        plain += f"\n\n---\n{token_footer}"

    return full_html, plain.strip()


def build_reply_subject(original_subject: str) -> str:
    """Сформировать тему ответа (Re: ...)."""
    subject = original_subject.strip()
    if not subject:
        return "Re: Запрос к 1С OData"
    if subject.lower().startswith("re:"):
        return subject
    return f"Re: {subject}"
