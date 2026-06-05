#!/usr/bin/env python3
"""Pydantic-модели для analytics-плана (multi-query, join, chart)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class AnalyticsSubQuery(BaseModel):
    """Один OData-запрос в analytics-плане."""

    alias: str
    entity: str
    filter: str | None = None
    select: str | None = None
    orderby: str | None = None
    top: int = 200
    skip: int = 0
    expand: str | None = None


class JoinSpec(BaseModel):
    """Спецификация объединения двух наборов данных."""

    left: str
    right: str
    left_on: str
    right_on: str
    how: Literal["left", "right", "inner", "outer"] = "left"


class AggregateSpec(BaseModel):
    """Группировка и агрегация."""

    group_by: list[str] = Field(default_factory=list)
    agg: dict[str, Literal["sum", "count", "mean", "min", "max"]] = Field(default_factory=dict)


class ChartSpec(BaseModel):
    """Спецификация графика."""

    type: Literal["bar", "line", "pie", "scatter"] = "bar"
    x: str
    y: str | None = None
    title: str = ""


class VisualizationDecision(BaseModel):
    """Решение субагента визуализации."""

    show_chart: bool = False
    show_table: bool = True
    chart: ChartSpec | None = None
    reason: str = ""
    source: Literal["rules", "ai"] = "rules"


class AnalyticsPlan(BaseModel):
    """План analytics-обработки от AI Step 1."""

    mode: Literal["analytics"] = "analytics"
    queries: list[AnalyticsSubQuery]
    joins: list[JoinSpec] = Field(default_factory=list)
    aggregate: AggregateSpec | None = None
    chart: ChartSpec | None = None
    explanation: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnalyticsPlan:
        """Создать план из JSON-ответа AI."""
        queries_raw = data.get("queries") or []
        queries = [
            AnalyticsSubQuery(
                alias=q.get("alias", f"q{i}"),
                entity=q.get("entity", ""),
                filter=q.get("filter"),
                select=_coerce_select(q.get("select")),
                orderby=q.get("orderby"),
                top=int(q.get("top") or 200),
                skip=int(q.get("skip") or 0),
                expand=q.get("expand"),
            )
            for i, q in enumerate(queries_raw)
            if isinstance(q, dict)
        ]

        joins_raw = data.get("joins") or []
        joins = [JoinSpec(**j) for j in joins_raw if isinstance(j, dict)]

        aggregate = None
        agg_raw = data.get("aggregate")
        if isinstance(agg_raw, dict):
            aggregate = AggregateSpec(
                group_by=agg_raw.get("group_by") or [],
                agg=agg_raw.get("agg") or {},
            )

        chart = None
        chart_raw = data.get("chart")
        if isinstance(chart_raw, dict) and chart_raw.get("x"):
            chart = ChartSpec(
                type=chart_raw.get("type", "bar"),
                x=chart_raw["x"],
                y=chart_raw.get("y"),
                title=chart_raw.get("title", ""),
            )

        return cls(
            mode="analytics",
            queries=queries,
            joins=joins,
            aggregate=aggregate,
            chart=chart,
            explanation=data.get("explanation") or "",
        )

    @field_validator("queries")
    @classmethod
    def _non_empty_queries(cls, v: list[AnalyticsSubQuery]) -> list[AnalyticsSubQuery]:
        if not v:
            raise ValueError("analytics-план должен содержать хотя бы один query")
        for q in v:
            if not q.entity:
                raise ValueError(f"Не указана entity для alias={q.alias}")
        return v

    def to_history_ctx(self) -> dict[str, Any]:
        """Сериализовать контекст для истории диалога."""
        return {
            "mode": "analytics",
            "queries": [q.model_dump(exclude_none=True) for q in self.queries],
            "joins": [j.model_dump() for j in self.joins],
            "aggregate": self.aggregate.model_dump() if self.aggregate else None,
            "chart": self.chart.model_dump() if self.chart else None,
            "explanation": self.explanation,
        }


def _coerce_select(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return ",".join(str(item) for item in value if item)
    return str(value)
