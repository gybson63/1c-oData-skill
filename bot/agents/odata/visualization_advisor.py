#!/usr/bin/env python3
"""Субагент выбора формата ответа: таблица, диаграмма или оба.

Используется после выполнения analytics-плана, когда данные уже в DataFrame.
Сначала применяются быстрые правила, при неоднозначности — короткий вызов AI.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd

from bot.agents.odata.analytics_models import AnalyticsPlan, ChartSpec, VisualizationDecision
from bot.agents.odata.chart_type_optimizer import optimize_chart_type
from bot.agents.odata.prompts import VISUALIZATION_ADVISOR_SYSTEM
from bot.agents.odata.tool_resolver import _extract_json

if TYPE_CHECKING:
    from bot.agents.odata.ai_service import AIService

log = logging.getLogger(__name__)

_TABLE_ONLY_HINTS = (
    "таблиц",
    "список",
    "списком",
    "перечень",
    "построчно",
    "детал",
    "выгруз",
    "все поля",
    "всех полей",
    "без графика",
    "без диаграмм",
)

_CHART_HINTS = (
    "график",
    "диаграмм",
    "chart",
    "diagram",
    "визуал",
    "динамик",
    "тренд",
    "распредел",
    "дол",
    "структур",
    "топ-",
    "топ ",
    "сравн",
    "численност",
    "по подраздел",
    "по организац",
    "по месяц",
)


class VisualizationAdvisor:
    """Решает, показывать диаграмму, таблицу или оба формата."""

    def __init__(self, *, pie_max_categories: int = 10, max_categories: int = 30) -> None:
        self._pie_max_categories = pie_max_categories
        self._max_categories = max_categories

    async def advise(
        self,
        ai: AIService | None,
        *,
        user_query: str,
        df: pd.DataFrame,
        plan: AnalyticsPlan,
        chat_id: int | None = None,
    ) -> VisualizationDecision:
        """Выбрать формат представления результата analytics."""
        query = (user_query or "").lower()

        if _matches_hints(query, _TABLE_ONLY_HINTS):
            return VisualizationDecision(
                show_chart=False,
                show_table=True,
                chart=None,
                reason="Пользователь явно запросил табличный/построчный формат",
                source="rules",
            )

        if df.empty:
            return VisualizationDecision(
                show_chart=False,
                show_table=True,
                reason="Нет данных для графика",
                source="rules",
            )

        rule_decision = self._rule_based_decision(query, df, plan)
        if rule_decision is not None and rule_decision.source == "rules" and not self._is_ambiguous(query, df, plan):
            return self._finalize_decision(rule_decision, df, plan, user_query)

        if ai is not None:
            ai_decision = await self._ai_decision(ai, user_query=user_query, df=df, plan=plan, chat_id=chat_id)
            if ai_decision is not None:
                return self._finalize_decision(ai_decision, df, plan, user_query)

        if rule_decision is not None:
            return self._finalize_decision(rule_decision, df, plan, user_query)

        return VisualizationDecision(
            show_chart=False,
            show_table=True,
            reason="Недостаточно оснований для диаграммы",
            source="rules",
        )

    def _rule_based_decision(
        self,
        query: str,
        df: pd.DataFrame,
        plan: AnalyticsPlan,
    ) -> VisualizationDecision | None:
        wants_chart = _matches_hints(query, _CHART_HINTS)
        chart = plan.chart

        if chart and _is_chartable(df, chart, max_categories=self._max_categories):
            return VisualizationDecision(
                show_chart=True,
                show_table=not wants_chart,
                chart=chart,
                reason="В плане analytics указан график и данные подходят",
                source="rules",
            )

        proposed = self._propose_chart_from_aggregate(df, plan)
        if wants_chart and proposed and _is_chartable(df, proposed, max_categories=self._max_categories):
            return VisualizationDecision(
                show_chart=True,
                show_table=True,
                chart=proposed,
                reason="Запрос про визуализацию, предложен график по агрегации",
                source="rules",
            )

        if plan.aggregate and plan.aggregate.group_by and not wants_chart:
            n_rows = len(df)
            n_cols = len(df.columns)
            if n_rows <= 30 and n_cols <= 8 and not chart:
                return VisualizationDecision(
                    show_chart=False,
                    show_table=True,
                    reason="Сводная таблица небольшого размера — достаточно таблицы",
                    source="rules",
                )

        if wants_chart and proposed:
            return VisualizationDecision(
                show_chart=True,
                show_table=True,
                chart=proposed,
                reason="Запрос про график, но данные ограничены",
                source="rules",
            )

        if chart and not _is_chartable(df, chart, max_categories=self._max_categories):
            return VisualizationDecision(
                show_chart=False,
                show_table=True,
                chart=None,
                reason="Данные не подходят для графика из плана",
                source="rules",
            )

        return None

    def _is_ambiguous(self, query: str, df: pd.DataFrame, plan: AnalyticsPlan) -> bool:
        """Нужен ли вызов AI (нет явных ключевых слов, есть агрегация)."""
        if _matches_hints(query, _TABLE_ONLY_HINTS) or _matches_hints(query, _CHART_HINTS):
            return False
        if plan.chart:
            return False
        if not plan.aggregate or not plan.aggregate.group_by:
            return False
        return 2 <= len(df) <= self._max_categories

    async def _ai_decision(
        self,
        ai: AIService,
        *,
        user_query: str,
        df: pd.DataFrame,
        plan: AnalyticsPlan,
        chat_id: int | None,
    ) -> VisualizationDecision | None:
        profile = _dataframe_profile(df, plan)
        messages = [
            {"role": "system", "content": VISUALIZATION_ADVISOR_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Запрос пользователя: {user_query}\n\n"
                    f"Профиль данных:\n{json.dumps(profile, ensure_ascii=False, indent=2)}"
                ),
            },
        ]
        try:
            content = await ai.visualization_advise(messages, chat_id=chat_id)
        except Exception as e:
            log.warning("VisualizationAdvisor AI failed: %s", e)
            return None

        parsed = _extract_json(content)
        if not parsed:
            log.warning("VisualizationAdvisor: не удалось разобрать JSON: %s", content[:300])
            return None

        return _decision_from_dict(parsed)

    def _finalize_decision(
        self,
        decision: VisualizationDecision,
        df: pd.DataFrame,
        plan: AnalyticsPlan,
        user_query: str,
    ) -> VisualizationDecision:
        chart = decision.chart
        if decision.show_chart and chart and _is_chartable(df, chart, max_categories=self._max_categories):
            chart = optimize_chart_type(
                df,
                chart,
                plan,
                user_query=user_query,
                pie_max_categories=self._pie_max_categories,
            )
            return decision.model_copy(update={"chart": chart})
        if decision.show_chart and (not chart or not _is_chartable(df, chart, max_categories=self._max_categories)):
            return decision.model_copy(
                update={
                    "show_chart": False,
                    "chart": None,
                    "reason": decision.reason + " (график отключён: данные не подходят)",
                }
            )
        return decision

    def _propose_chart_from_aggregate(self, df: pd.DataFrame, plan: AnalyticsPlan) -> ChartSpec | None:
        if not plan.aggregate or not plan.aggregate.group_by:
            return None
        group_col = plan.aggregate.group_by[0]
        if group_col not in df.columns:
            return None

        y_col: str | None = None
        agg_type: Literal["sum", "count", "mean", "min", "max"] | None = None
        if plan.aggregate.agg:
            for col, fn in plan.aggregate.agg.items():
                if col in df.columns:
                    y_col = col
                    agg_type = fn
                    break
            if y_col is None and len(plan.aggregate.agg) == 1:
                y_col, agg_type = next(iter(plan.aggregate.agg.items()))
        if not y_col:
            numeric = [c for c in df.columns if c != group_col and pd.api.types.is_numeric_dtype(df[c])]
            y_col = numeric[0] if numeric else None

        if not y_col or y_col not in df.columns:
            return None

        n_cat = int(df[group_col].nunique())
        chart_type: Literal["bar", "line", "pie", "scatter"] = "bar"
        if agg_type == "count" and 2 <= n_cat <= self._pie_max_categories:
            chart_type = "pie"

        title = plan.explanation or plan.chart.title if plan.chart else "Аналитика"
        return ChartSpec(type=chart_type, x=group_col, y=y_col, title=title[:80])


def _decision_from_dict(data: dict[str, Any]) -> VisualizationDecision:
    chart = None
    chart_raw = data.get("chart")
    if isinstance(chart_raw, dict) and chart_raw.get("x"):
        chart = ChartSpec(
            type=chart_raw.get("type", "bar"),
            x=chart_raw["x"],
            y=chart_raw.get("y"),
            title=chart_raw.get("title", ""),
        )
    return VisualizationDecision(
        show_chart=bool(data.get("show_chart")),
        show_table=bool(data.get("show_table", True)),
        chart=chart,
        reason=str(data.get("reason") or ""),
        source="ai",
    )


def _dataframe_profile(df: pd.DataFrame, plan: AnalyticsPlan) -> dict[str, Any]:
    sample = df.head(5).astype(str).to_dict(orient="records")
    dtypes = {col: str(dtype) for col, dtype in df.dtypes.items()}
    profile: dict[str, Any] = {
        "rows": len(df),
        "columns": list(df.columns),
        "dtypes": dtypes,
        "sample": sample,
        "aggregate": plan.aggregate.model_dump() if plan.aggregate else None,
        "planned_chart": plan.chart.model_dump() if plan.chart else None,
        "explanation": plan.explanation,
    }
    if plan.chart and plan.chart.x in df.columns:
        profile["x_unique"] = int(df[plan.chart.x].nunique())
    return profile


def _is_chartable(df: pd.DataFrame, chart: ChartSpec, *, max_categories: int) -> bool:
    if df.empty or chart.x not in df.columns:
        return False
    work = df.dropna(subset=[chart.x])
    if len(work) < 2:
        return False
    n_cat = int(work[chart.x].nunique())
    if chart.type in {"bar", "pie"} and n_cat > max_categories:
        return False
    if chart.y and chart.y in df.columns:
        series = pd.to_numeric(df[chart.y], errors="coerce")
        return bool(pd.api.types.is_numeric_dtype(series))
    numeric = [c for c in df.columns if c != chart.x and pd.api.types.is_numeric_dtype(df[c])]
    return bool(numeric)


def _matches_hints(text: str, hints: tuple[str, ...]) -> bool:
    return any(hint in text for hint in hints)
