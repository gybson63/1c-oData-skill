#!/usr/bin/env python3
"""Рендеринг графиков из pandas DataFrame (matplotlib PNG, plotly HTML)."""

from __future__ import annotations

import platform
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import plotly.graph_objects as go  # noqa: E402

from bot.agents.odata.analytics_models import ChartSpec

ChartData = tuple[pd.DataFrame, ChartSpec]


def _configure_matplotlib_fonts() -> None:
    if platform.system() == "Windows":
        plt.rcParams["font.sans-serif"] = ["Segoe UI", "Arial", "DejaVu Sans"]
    else:
        plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "Liberation Sans", "Arial"]
    plt.rcParams["axes.unicode_minus"] = False


def prepare_chart_data(
    df: pd.DataFrame,
    chart: ChartSpec,
    *,
    max_categories: int = 30,
) -> tuple[pd.DataFrame, str]:
    """Подготовить данные для графика: валидация колонок и обрезка категорий.

    Returns:
        (dataframe, y_column_name)
    """
    if df.empty:
        raise ValueError("Нет данных для построения графика")

    if chart.x not in df.columns:
        raise ValueError(f"Колонка X не найдена: {chart.x}")

    y_col = chart.y or ""
    chart_type = chart.type

    if chart_type in {"bar", "line", "scatter"}:
        if not y_col or y_col not in df.columns:
            raise ValueError(f"Колонка Y не найдена: {y_col}")
    elif chart_type == "pie":
        if not y_col or y_col not in df.columns:
            y_col = "_count"

    work = df.copy()
    if y_col == "_count":
        work["_count"] = 1
    elif y_col in work.columns:
        work[y_col] = pd.to_numeric(work[y_col], errors="coerce")

    work = work.dropna(subset=[chart.x])
    if y_col != "_count" and y_col in work.columns:
        work = work.dropna(subset=[y_col])

    if chart_type in {"bar", "pie"} and len(work) > max_categories:
        if y_col in work.columns:
            work = work.nlargest(max_categories, y_col)
        else:
            work = work.head(max_categories)

    return work, y_col


def render_png(
    df: pd.DataFrame,
    chart: ChartSpec,
    *,
    max_categories: int = 30,
) -> bytes:
    """Построить PNG-график (matplotlib)."""
    _configure_matplotlib_fonts()
    data, y_col = prepare_chart_data(df, chart, max_categories=max_categories)

    fig, ax = plt.subplots(figsize=(10, 6))
    title = chart.title or "График"

    if chart.type == "bar":
        ax.bar(data[chart.x].astype(str), data[y_col], color="#4472C4")
        ax.set_xlabel(chart.x)
        ax.set_ylabel(y_col)
        plt.xticks(rotation=45, ha="right")
    elif chart.type == "line":
        ax.plot(data[chart.x].astype(str), data[y_col], marker="o", color="#4472C4")
        ax.set_xlabel(chart.x)
        ax.set_ylabel(y_col)
        plt.xticks(rotation=45, ha="right")
    elif chart.type == "scatter":
        ax.scatter(data[chart.x], data[y_col], color="#4472C4")
        ax.set_xlabel(chart.x)
        ax.set_ylabel(y_col)
    elif chart.type == "pie":
        ax.pie(
            data[y_col],
            labels=data[chart.x].astype(str),
            autopct="%1.1f%%",
            startangle=90,
        )
        ax.axis("equal")

    ax.set_title(title)
    fig.tight_layout()

    import io

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    buffer.seek(0)
    return buffer.read()


def render_html(
    df: pd.DataFrame,
    chart: ChartSpec,
    *,
    max_categories: int = 30,
) -> str:
    """Построить интерактивный HTML-график (plotly)."""
    data, y_col = prepare_chart_data(df, chart, max_categories=max_categories)
    title = chart.title or "График"

    if chart.type == "bar":
        fig = px.bar(data, x=chart.x, y=y_col, title=title)
    elif chart.type == "line":
        fig = px.line(data, x=chart.x, y=y_col, title=title, markers=True)
    elif chart.type == "scatter":
        fig = px.scatter(data, x=chart.x, y=y_col, title=title)
    elif chart.type == "pie":
        fig = go.Figure(
            data=[
                go.Pie(
                    labels=data[chart.x].astype(str),
                    values=data[y_col],
                    title=title,
                )
            ]
        )
    else:
        raise ValueError(f"Неподдерживаемый тип графика: {chart.type}")

    return fig.to_html(include_plotlyjs="cdn", full_html=True)


def chart_spec_from_dict(data: dict[str, Any]) -> ChartSpec:
    """Создать ChartSpec из словаря."""
    return ChartSpec(
        type=data.get("type", "bar"),
        x=data["x"],
        y=data.get("y"),
        title=data.get("title", ""),
    )
