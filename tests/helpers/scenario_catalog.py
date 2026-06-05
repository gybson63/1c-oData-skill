"""Загрузка каталога сценариев tests/scenarios/catalog.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CATALOG_PATH = Path(__file__).resolve().parent.parent / "scenarios" / "catalog.yaml"


@dataclass
class Scenario:
    id: str
    layer: str
    question: str
    asserts: list[str] = field(default_factory=list)
    status: str = "planned"
    owner: str = ""
    notes: str = ""
    domain: str = ""
    report_analog: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def load_catalog(path: Path | None = None) -> list[Scenario]:
    """Прочитать catalog.yaml."""
    path = path or CATALOG_PATH
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = raw.get("scenarios", [])
    result: list[Scenario] = []
    for item in items:
        known = {"id", "layer", "question", "asserts", "status", "owner", "notes", "domain", "report_analog"}
        extra = {k: v for k, v in item.items() if k not in known}
        result.append(
            Scenario(
                id=item["id"],
                layer=item.get("layer", "e2e"),
                question=item.get("question", ""),
                asserts=item.get("asserts", []),
                status=item.get("status", "planned"),
                owner=item.get("owner", ""),
                notes=item.get("notes", ""),
                domain=item.get("domain", ""),
                report_analog=item.get("report_analog", ""),
                extra=extra,
            )
        )
    return result


def scenarios_by_layer(layer: str, *, status: str | None = "implemented") -> list[Scenario]:
    """Сценарии слоя с опциональным фильтром по статусу."""
    items = load_catalog()
    out = [s for s in items if s.layer == layer]
    if status is not None:
        out = [s for s in out if s.status == status]
    return out


def scenario_by_id(scenario_id: str, *, path: Path | None = None) -> Scenario:
    """Найти сценарий по id; KeyError если не найден."""
    for item in load_catalog(path):
        if item.id == scenario_id:
            return item
    raise KeyError(scenario_id)
