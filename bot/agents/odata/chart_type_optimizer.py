#!/usr/bin/env python3
"""Подбор оптимального типа диаграммы для analytics-плана."""

from __future__ import annotations

import logging
import re
from typing import Literal, cast

import pandas as pd

from bot.agents.odata.analytics_models import AnalyticsPlan, ChartSpec

log = logging.getLogger(__name__)

ChartType = Literal["bar", "line", "pie", "scatter"]

PIE_MAX_CATEGORIES_DEFAULT = 10

_TEMPORAL_NAME_HINTS = (
    "date",
    "period",
    "период",
    "дата",
    "месяц",
    "month",
    "year",
    "год",
    "week",
    "недел",
    "day",
    "день",
    "time",
    "время",
    "квартал",
    "quarter",
)

_DISTRIBUTION_HINTS = (
    "распредел",
    "дол",
    "структур",
    "состав",
    "процент",
    "доля",
    "share",
    "proportion",
    "кругов",
    "сектор",
    "удельн",
)

_COMPARISON_HINTS = (
    "топ",
    "top-",
    "top ",
    "сравн",
    "compare",
    "рейтинг",
    "rank",
    "столбч",
    "гистограм",
    "bar chart",
)

_TREND_HINTS = (
    "динамик",
    "тренд",
    "trend",
    "во времени",
    "по месяц",
    "по год",
    "изменен",
    "рост",
    "паден",
)

_SCATTER_HINTS = ("коррел", "correl", "scatter", "зависимост")


def optimize_chart_type(
    df: pd.DataFrame,
    chart: ChartSpec,
    plan: AnalyticsPlan,
    *,
    user_query: str | None = None,
    pie_max_categories: int = PIE_MAX_CATEGORIES_DEFAULT,
) -> ChartSpec:
    """Подобрать тип диаграммы по данным, агрегации и формулировке запроса."""
    if df.empty:
        return chart

    query = (user_query or "").lower()
    y_col = _resolve_y_column(df, chart)
    if not y_col or chart.x not in df.columns:
        return chart

    work = df[[chart.x, y_col]].dropna()
    if work.empty:
        return chart

    n_categories = int(work[chart.x].nunique())
    temporal_x = _is_temporal_column(chart.x, work[chart.x])
    part_whole = _is_part_whole_chart(plan, y_col)
    non_negative = _values_non_negative(work[y_col])

    explicit_pie = chart.type == "pie" or _matches_hints(query, _DISTRIBUTION_HINTS)
    explicit_bar = _matches_hints(query, _COMPARISON_HINTS)
    explicit_line = _matches_hints(query, _TREND_HINTS)
    explicit_scatter = _matches_hints(query, _SCATTER_HINTS)

    new_type: ChartType = chart.type

    if chart.type == "pie" and n_categories > pie_max_categories:
        new_type = "bar"
    elif explicit_scatter and _is_numeric(work[chart.x]) and _is_numeric(work[y_col]):
        new_type = "scatter"
    elif temporal_x and not explicit_pie and not explicit_bar:
        new_type = "line"
    elif _should_use_pie(
        chart_type=chart.type,
        part_whole=part_whole,
        non_negative=non_negative,
        n_categories=n_categories,
        temporal_x=temporal_x,
        explicit_pie=explicit_pie,
        explicit_bar=explicit_bar,
        primary_agg_count=_primary_agg_is_count(plan, y_col),
        pie_max_categories=pie_max_categories,
    ):
        new_type = "pie"
    elif chart.type == "line" and not temporal_x and not explicit_line:
        new_type = "bar"

    if new_type != chart.type:
        log.info(
            "Chart type optimized: %s -> %s (categories=%d, temporal=%s, part_whole=%s)",
            chart.type,
            new_type,
            n_categories,
            temporal_x,
            part_whole,
        )
        return cast(ChartSpec, chart.model_copy(update={"type": new_type}))
    return chart


def _should_use_pie(
    *,
    chart_type: ChartType,
    part_whole: bool,
    non_negative: bool,
    n_categories: int,
    temporal_x: bool,
    explicit_pie: bool,
    explicit_bar: bool,
    primary_agg_count: bool,
    pie_max_categories: int,
) -> bool:
    if not part_whole or not non_negative or temporal_x or explicit_bar:
        return False
    if n_categories < 2 or n_categories > pie_max_categories:
        return False
    if explicit_pie or primary_agg_count:
        return True
    return chart_type == "pie"


def _resolve_y_column(df: pd.DataFrame, chart: ChartSpec) -> str | None:
    if chart.y and chart.y in df.columns:
        return chart.y
    numeric_cols = [col for col in df.columns if col != chart.x and pd.api.types.is_numeric_dtype(df[col])]
    return numeric_cols[0] if numeric_cols else None


def _is_part_whole_chart(plan: AnalyticsPlan, y_col: str) -> bool:
    if not plan.aggregate:
        return False
    if len(plan.aggregate.group_by) != 1:
        return False
    if not plan.aggregate.agg:
        return True
    if y_col in plan.aggregate.agg:
        return plan.aggregate.agg[y_col] in {"count", "sum"}
    if len(plan.aggregate.agg) == 1:
        return next(iter(plan.aggregate.agg.values())) in {"count", "sum"}
    return False


def _primary_agg_is_count(plan: AnalyticsPlan, y_col: str) -> bool:
    if not plan.aggregate or not plan.aggregate.agg:
        return False
    if y_col in plan.aggregate.agg:
        return plan.aggregate.agg[y_col] == "count"
    if len(plan.aggregate.agg) == 1:
        return next(iter(plan.aggregate.agg.values())) == "count"
    return False


def _is_temporal_column(name: str, series: pd.Series) -> bool:
    lower = name.lower()
    name_suggests_time = any(hint in lower for hint in _TEMPORAL_NAME_HINTS)

    sample = series.dropna().head(20)
    if sample.empty:
        return name_suggests_time

    if pd.api.types.is_datetime64_any_dtype(sample):
        return True

    text_sample = sample.astype(str).str.strip()
    date_like = re.compile(
        r"^\d{4}-\d{2}-\d{2}|^\d{2}\.\d{2}\.\d{4}|^\d{4}-\d{2}|^\d{2}\.\d{4}$",
    )
    if text_sample.map(lambda value: bool(date_like.match(value))).mean() >= 0.8:
        return True

    if not name_suggests_time:
        return False

    parsed = pd.to_datetime(text_sample, errors="coerce", dayfirst=True)
    return bool(parsed.notna().mean() >= 0.8)


def _is_numeric(series: pd.Series) -> bool:
    return bool(pd.api.types.is_numeric_dtype(pd.to_numeric(series, errors="coerce")))


def _values_non_negative(series: pd.Series) -> bool:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return False
    return bool((numeric >= 0).all())


def _matches_hints(text: str, hints: tuple[str, ...]) -> bool:
    return any(hint in text for hint in hints)
