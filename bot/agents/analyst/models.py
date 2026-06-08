#!/usr/bin/env python3
"""Модели результата анализа метаданных."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class MetadataObject:
    """Один объект метаданных в результате анализа."""

    meta_type: str
    name: str
    odata_entity: str = ""
    role: str = "primary"  # primary | join | source | avoid
    reason: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MetadataObject:
        return cls(
            meta_type=str(data.get("meta_type", "")),
            name=str(data.get("name", "")),
            odata_entity=str(data.get("odata_entity", "")),
            role=str(data.get("role", "primary")),
            reason=str(data.get("reason", "")),
        )


@dataclass
class MetadataBrief:
    """Структурированный результат работы аналитика."""

    intent: str = ""
    primary_objects: list[MetadataObject] = field(default_factory=list)
    secondary_objects: list[MetadataObject] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    conf_doc_queries: list[str] = field(default_factory=list)
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MetadataBrief:
        def _objs(key: str) -> list[MetadataObject]:
            raw = data.get(key) or []
            return [MetadataObject.from_dict(o) for o in raw if isinstance(o, dict)]

        avoid_raw = data.get("avoid") or []
        avoid = [str(x) for x in avoid_raw]
        queries_raw = data.get("conf_doc_queries") or []
        return cls(
            intent=str(data.get("intent", "")),
            primary_objects=_objs("primary_objects"),
            secondary_objects=_objs("secondary_objects"),
            avoid=avoid,
            conf_doc_queries=[str(q) for q in queries_raw],
            notes=str(data.get("notes", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_prompt_block(self) -> str:
        """Текстовый блок для вставки в промпт OData Step 1."""
        lines = [
            f"Intent: {self.intent or '(не определён)'}",
        ]
        if self.primary_objects:
            lines.append("Primary objects:")
            for obj in self.primary_objects:
                entity = obj.odata_entity or f"{obj.meta_type}.{obj.name}"
                lines.append(f"  - {entity} ({obj.role}): {obj.reason}")
        if self.secondary_objects:
            lines.append("Secondary objects:")
            for obj in self.secondary_objects:
                entity = obj.odata_entity or f"{obj.meta_type}.{obj.name}"
                lines.append(f"  - {entity} ({obj.role}): {obj.reason}")
        if self.avoid:
            lines.append("Avoid: " + ", ".join(self.avoid))
        if self.notes:
            lines.append(f"Notes: {self.notes}")
        return "\n".join(lines)

    def to_html_summary(self) -> str:
        """HTML-ответ для standalone режима /analyze."""
        from bot.utils import esc_html

        parts = ["<b>📋 Анализ метаданных</b>"]
        if self.intent:
            parts.append(f"\n<b>Intent:</b> {esc_html(self.intent)}")

        def _list_objects(title: str, objs: list[MetadataObject]) -> None:
            if not objs:
                return
            parts.append(f"\n<b>{title}:</b>")
            for obj in objs:
                entity = obj.odata_entity or f"{obj.meta_type}.{obj.name}"
                reason = f" — {esc_html(obj.reason)}" if obj.reason else ""
                parts.append(f"• <code>{esc_html(entity)}</code> ({esc_html(obj.role)}){reason}")

        _list_objects("Основные объекты", self.primary_objects)
        _list_objects("Дополнительные", self.secondary_objects)

        if self.avoid:
            parts.append("\n<b>Избегать:</b> " + ", ".join(f"<code>{esc_html(a)}</code>" for a in self.avoid))
        if self.notes:
            parts.append(f"\n<i>{esc_html(self.notes)}</i>")
        return "\n".join(parts)
