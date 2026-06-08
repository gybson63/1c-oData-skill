#!/usr/bin/env python3
"""Выполнение analytics-плана: multi-query OData → pandas join/aggregate."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd

from bot.agents.odata.analytics_models import AnalyticsPlan
from bot.agents.odata.analytics_plan_utils import align_dataframe_to_plan, normalize_analytics_plan
from bot.agents.odata.analytics_reference_labels import resolve_reference_labels_in_dataframe
from bot.agents.odata.query_executor import QueryExecutor
from bot.agents.odata.response_parser import resolve_references_for_analytics
from bot_lib.dataframe import aggregate_dataframe, merge_dataframes, records_to_dataframe

log = logging.getLogger(__name__)


@dataclass
class AnalyticsResult:
    """Результат выполнения analytics-плана."""

    dataframe: pd.DataFrame
    row_counts: dict[str, int]
    plan: AnalyticsPlan


class AnalyticsExecutor:
    """Загрузка нескольких OData-наборов и объединение в pandas."""

    def __init__(
        self,
        executor: QueryExecutor,
        *,
        metadata: Any | None = None,
        max_records: int = 500,
        max_joins: int = 3,
    ) -> None:
        self._executor = executor
        self._metadata = metadata
        self._max_records = max_records
        self._max_joins = max_joins

    async def execute(self, plan: AnalyticsPlan, *, user_query: str | None = None) -> AnalyticsResult:
        """Выполнить analytics-план."""
        plan = normalize_analytics_plan(plan.model_copy(deep=True))

        if len(plan.joins) > self._max_joins:
            raise ValueError(f"Слишком много join-ов (максимум {self._max_joins})")

        tasks = [self._fetch_query(q) for q in plan.queries]
        results = await asyncio.gather(*tasks)

        dfs: dict[str, pd.DataFrame] = {}
        row_counts: dict[str, int] = {}
        for alias, df, count in results:
            dfs[alias] = df
            row_counts[alias] = count

        if plan.joins:
            df = merge_dataframes(dfs, [j.model_dump() for j in plan.joins])
        elif len(dfs) == 1:
            df = next(iter(dfs.values()))
        else:
            raise ValueError("Несколько queries без joins — укажите joins")

        df = align_dataframe_to_plan(df, plan)

        label_columns: list[str] = []
        if plan.aggregate:
            label_columns.extend(plan.aggregate.group_by)
        if plan.chart and plan.chart.x not in label_columns:
            label_columns.append(plan.chart.x)
        if label_columns:
            df = await resolve_reference_labels_in_dataframe(
                df,
                label_columns,
                self._executor,
                self._metadata,
            )

        if plan.aggregate and plan.aggregate.group_by:
            df = aggregate_dataframe(
                df,
                plan.aggregate.group_by,
                plan.aggregate.agg,
            )

        if plan.chart:
            from bot.agents.odata.chart_type_optimizer import optimize_chart_type

            plan.chart = optimize_chart_type(
                df,
                plan.chart,
                plan,
                user_query=user_query,
            )

        return AnalyticsResult(dataframe=df, row_counts=row_counts, plan=plan)

    async def _fetch_query(self, query) -> tuple[str, pd.DataFrame, int]:
        top = min(query.top, self._max_records)
        records, total = await self._executor.execute(
            entity=query.entity,
            filter_expr=query.filter,
            select=query.select,
            orderby=query.orderby,
            top=top,
            skip=query.skip or None,
            expand=query.expand,
        )
        resolved = resolve_references_for_analytics(records)
        df = records_to_dataframe(resolved)
        log.info(
            "Analytics query alias=%s entity=%s rows=%d total=%d",
            query.alias,
            query.entity,
            len(df),
            total,
        )
        return query.alias, df, len(records)
