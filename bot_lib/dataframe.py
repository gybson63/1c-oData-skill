#!/usr/bin/env python3
"""Преобразование OData-записей в pandas DataFrame и операции join/aggregate."""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

JoinSpec = Mapping[str, Any]


def records_to_dataframe(
    records: list[dict[str, Any]],
    *,
    normalize: bool = True,
) -> pd.DataFrame:
    """Преобразовать список записей OData в DataFrame.

    Args:
        records: элементы массива ``value`` из OData-ответа.
        normalize: использовать ``pd.json_normalize`` для вложенных объектов ($expand).
    """
    if not records:
        return pd.DataFrame()

    if normalize:
        return pd.json_normalize(records, sep="_")

    return pd.DataFrame(records)


def merge_dataframes(
    dfs: dict[str, pd.DataFrame],
    joins: Sequence[JoinSpec],
) -> pd.DataFrame:
    """Последовательно объединить именованные DataFrame по спецификациям join."""
    if not dfs:
        return pd.DataFrame()

    if not joins:
        aliases = list(dfs.keys())
        if len(aliases) == 1:
            return dfs[aliases[0]].copy()
        raise ValueError("Несколько наборов данных без joins — укажите joins в плане analytics")

    result: pd.DataFrame | None = None
    for spec in joins:
        left_alias = spec["left"]
        right_alias = spec["right"]
        if left_alias not in dfs:
            raise ValueError(f"Неизвестный alias в join: {left_alias}")
        if right_alias not in dfs:
            raise ValueError(f"Неизвестный alias в join: {right_alias}")

        left_on = spec.get("left_on")
        right_on = spec.get("right_on")
        how = spec.get("how", "left")

        if result is None:
            left_df = dfs[left_alias]
        else:
            left_df = result

        right_df = dfs[right_alias]
        result = pd.merge(
            left_df,
            right_df,
            left_on=left_on,
            right_on=right_on,
            how=how,
            suffixes=("", f"_{right_alias}"),
        )

    return result if result is not None else pd.DataFrame()


def aggregate_dataframe(
    df: pd.DataFrame,
    group_by: list[str] | None,
    agg: Mapping[str, str] | None,
) -> pd.DataFrame:
    """Сгруппировать и агрегировать DataFrame."""
    if df.empty or not group_by or not agg:
        return df

    missing = [col for col in group_by if col not in df.columns]
    if missing:
        raise ValueError(f"Поля group_by не найдены в данных: {', '.join(missing)}")

    grouped = df.groupby(group_by, dropna=False)
    agg_map: dict[str, str] = {}
    synthetic_count: str | None = None

    for col, func in agg.items():
        if func not in {"sum", "count", "mean", "min", "max"}:
            raise ValueError(f"Неподдерживаемая агрегация: {func}")
        if col not in df.columns:
            if func == "count":
                synthetic_count = col
                continue
            raise ValueError(f"Поле агрегации не найдено: {col}")
        agg_map[col] = func

    if synthetic_count and not agg_map:
        return grouped.size().reset_index(name=synthetic_count)

    if not agg_map and not synthetic_count:
        return df

    result = (
        grouped.agg(agg_map).reset_index() if agg_map else grouped.size().reset_index(name=synthetic_count or "count")
    )

    if synthetic_count and agg_map:
        sizes = grouped.size().reset_index(name=synthetic_count)
        result = result.merge(sizes, on=group_by, how="left")

    return result


def dataframe_to_csv(df: pd.DataFrame) -> str:
    """Сериализовать DataFrame в CSV (UTF-8)."""
    if df.empty:
        return ""
    buffer = io.StringIO()
    df.to_csv(buffer, index=False)
    return buffer.getvalue()


def dataframe_preview_html(df: pd.DataFrame, max_rows: int = 20) -> str:
    """HTML-таблица для Telegram/email (без полного HTML-документа)."""
    if df.empty:
        return "<i>Данные не найдены.</i>"

    preview = df.head(max_rows)
    rows_html: list[str] = []
    headers = preview.columns.tolist()
    header_cells = "".join(f"<th>{_esc_html(str(h))}</th>" for h in headers)
    rows_html.append(f"<tr>{header_cells}</tr>")

    for _, row in preview.iterrows():
        cells = "".join(f"<td>{_esc_html(_format_cell(row[h]))}</td>" for h in headers)
        rows_html.append(f"<tr>{cells}</tr>")

    table = "<table>" + "".join(rows_html) + "</table>"
    if len(df) > max_rows:
        table += f"\n<i>… и ещё {len(df) - max_rows} строк</i>"
    return table


def _format_cell(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, float):
        return f"{value:,.2f}".replace(",", " ")
    return str(value)


def _esc_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
