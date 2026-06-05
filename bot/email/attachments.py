#!/usr/bin/env python3
"""Извлечение текста из вложений email."""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from xml.etree import ElementTree

from bot.messages import Attachment

log = logging.getLogger(__name__)

# Максимальный размер вложения для обработки (10 МБ)
MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024
# Максимум символов текста из одного вложения
MAX_EXTRACTED_CHARS = 8000


def extract_text_from_attachment(att: Attachment) -> str:
    """Извлечь текстовое содержимое из вложения.

    Поддерживаемые форматы: plain text, CSV, JSON, XML, XLSX (базово), ZIP (первый текстовый файл).
    """
    if att.size > MAX_ATTACHMENT_SIZE:
        return f"[Вложение {att.filename} слишком большое ({att.size} байт), пропущено]"

    ct = att.content_type.lower()
    name = att.filename.lower()

    try:
        if ct.startswith("text/") or name.endswith((".txt", ".csv", ".json", ".xml", ".md", ".log", ".1c")):
            return _extract_text(att.data, name)

        if name.endswith(".csv") or ct == "text/csv":
            return _extract_csv(att.data)

        if name.endswith(".xlsx") or ct == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
            return _extract_xlsx(att.data, att.filename)

        if name.endswith(".zip") or ct == "application/zip":
            return _extract_zip(att.data)

        if name.endswith(".pdf") or ct == "application/pdf":
            return _extract_pdf(att.data, att.filename)

        # Неизвестный формат — попробовать как текст
        if _looks_like_text(att.data):
            return _extract_text(att.data, name)

        return f"[Вложение {att.filename} ({ct}): бинарный файл, содержимое недоступно]"
    except Exception as e:
        log.warning("Ошибка извлечения текста из %s: %s", att.filename, e)
        return f"[Вложение {att.filename}: ошибка чтения — {e}]"


def _looks_like_text(data: bytes) -> bool:
    if not data:
        return True
    sample = data[:512]
    non_printable = sum(1 for b in sample if b < 9 or (13 < b < 32 and b != 10))
    return non_printable / len(sample) < 0.1


def _extract_text(data: bytes, filename: str) -> str:
    for enc in ("utf-8", "cp1251", "latin-1"):
        try:
            text = data.decode(enc)
            return _truncate(f"--- {filename} ---\n{text}")
        except UnicodeDecodeError:
            continue
    return _truncate(f"--- {filename} ---\n{data.decode('utf-8', errors='replace')}")


def _extract_csv(data: bytes) -> str:
    for enc in ("utf-8", "cp1251", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", errors="replace")

    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return "[CSV: пустой файл]"

    # Показать заголовок + первые 30 строк
    lines = [" | ".join(row) for row in rows[:30]]
    result = "\n".join(lines)
    if len(rows) > 30:
        result += f"\n... (ещё {len(rows) - 30} строк)"
    return _truncate(result)


def _extract_xlsx(data: bytes, filename: str) -> str:
    """Базовое чтение XLSX через XML внутри ZIP (без openpyxl)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            # shared strings
            shared: list[str] = []
            if "xl/sharedStrings.xml" in zf.namelist():
                root = ElementTree.fromstring(zf.read("xl/sharedStrings.xml"))
                ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                for si in root.findall(".//m:si", ns):
                    texts = [t.text or "" for t in si.findall(".//m:t", ns)]
                    shared.append("".join(texts))

            # first sheet
            sheet_files = sorted(n for n in zf.namelist() if n.startswith("xl/worksheets/sheet"))
            if not sheet_files:
                return f"[XLSX {filename}: листы не найдены]"

            root = ElementTree.fromstring(zf.read(sheet_files[0]))
            ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            rows_out: list[str] = []
            for row in root.findall(".//m:sheetData/m:row", ns):
                cells: list[str] = []
                for cell in row.findall("m:c", ns):
                    ref_type = cell.get("t")
                    val_el = cell.find("m:v", ns)
                    if val_el is None or val_el.text is None:
                        cells.append("")
                    elif ref_type == "s":
                        idx = int(val_el.text)
                        cells.append(shared[idx] if idx < len(shared) else "")
                    else:
                        cells.append(val_el.text)
                if any(cells):
                    rows_out.append(" | ".join(cells))
                if len(rows_out) >= 30:
                    break

            result = f"--- {filename} (XLSX) ---\n" + "\n".join(rows_out)
            return _truncate(result)
    except Exception as e:
        return f"[XLSX {filename}: {e}]"


def _extract_zip(data: bytes) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist()[:5]:
            if name.endswith("/"):
                continue
            file_data = zf.read(name)
            if _looks_like_text(file_data):
                parts.append(_extract_text(file_data, name))
            if len("\n".join(parts)) > MAX_EXTRACTED_CHARS:
                break
    return _truncate("\n\n".join(parts) if parts else "[ZIP: текстовые файлы не найдены]")


def _extract_pdf(data: bytes, filename: str) -> str:
    """Простое извлечение текста из PDF (без OCR)."""
    try:
        import re as _re

        # Ищем текстовые блоки в PDF stream
        raw = data.decode("latin-1", errors="replace")
        texts = _re.findall(r"\(([^()\\]{2,200})\)", raw)
        texts += _re.findall(r"\[([^\]]{2,200})\]", raw)
        if texts:
            cleaned = " ".join(t.replace("\\n", "\n") for t in texts[:200])
            return _truncate(f"--- {filename} (PDF) ---\n{cleaned}")
        return f"[PDF {filename}: текст не извлечён (возможно, скан/OCR не поддерживается)]"
    except Exception as e:
        return f"[PDF {filename}: {e}]"


def _truncate(text: str) -> str:
    if len(text) <= MAX_EXTRACTED_CHARS:
        return text
    return text[:MAX_EXTRACTED_CHARS] + f"\n... (обрезано, всего {len(text)} символов)"


def format_attachments_for_context(attachments: list[Attachment]) -> str:
    """Сформировать блок текста из всех вложений для AI-контекста."""
    if not attachments:
        return ""

    parts: list[str] = ["\n\n--- Вложения ---"]
    for att in attachments:
        extracted = extract_text_from_attachment(att)
        parts.append(f"\n📎 {att.filename} ({att.content_type}, {att.size} байт):\n{extracted}")

    return "\n".join(parts)
