#!/usr/bin/env python3
"""Структуры данных для обработки OData-запросов.

Заменяяют набор разрозненных параметров и ContextVar единым типизированным
состоянием, которое проходит через весь pipeline обработки.

.. dataclass:`ODataQuery` — распарсенный OData-запрос от AI
.. dataclass:`ODataState` — полное состояние обработки одного сообщения
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ODataQuery:
    """Распарсенный OData-запрос, сформированный AI (Шаг 1)."""

    entity: str
    filter_expr: str | None = None
    select: str | None = None
    orderby: str | None = None
    top: int = 20
    skip: int = 0
    count: bool = False
    expand: str | None = None
    explanation: str | None = None

    # -- factories --

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ODataQuery:
        """Создать ODataQuery из JSON-ответа AI."""
        return cls(
            entity=data.get("entity", ""),
            filter_expr=data.get("filter"),
            select=data.get("select"),
            orderby=data.get("orderby"),
            top=int(data.get("top") or 20),
            skip=int(data.get("skip") or 0),
            count=bool(data.get("count", False)),
            explanation=data.get("explanation"),
        )

    def to_pagination_ctx(self) -> dict[str, Any]:
        """Сериализовать в контекст пагинации (для сохранения в историю)."""
        return {
            "entity": self.entity,
            "filter": self.filter_expr,
            "select": self.select,
            "orderby": self.orderby,
            "top": self.top,
            "skip": self.skip,
            "expand": self.expand,
            "explanation": self.explanation or "",
        }


@dataclass
class ODataState:
    """Полное состояние обработки одного запроса пользователя.

    Проходит через все этапы pipeline:
      ``build_query → resolve_tools → validate → execute → format``

    Атрибуты заполняются по мере продвижения по pipeline.
    """

    # -- Входные данные --
    user_text: str
    chat_id: int | None = None
    history: list[dict[str, str]] = field(default_factory=list)

    # -- Промежуточное состояние Шага 1 (AI → OData) --
    ai_messages: list[dict[str, Any]] = field(default_factory=list)
    ai_response_content: str = ""
    query: ODataQuery | None = None

    # -- Промежуточное состояние OData --
    records: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0
    auth_header: str = ""

    # -- Результат --
    answer_html: str = ""
    error: str | None = None

    # -- Пагинация --
    pagination_ctx: dict[str, Any] | None = None

    # -- Служебные флаги --
    tools_supported: bool = True

    # -- helpers --

    def finalize_history(
        self,
        max_turns: int,
        assistant_content: str | None = None,
    ) -> list[dict[str, str]]:
        """Добавить пару user/assistant в историю и обрезать по max_turns.

        Args:
            max_turns: максимальное количество пар (user + assistant).
            assistant_content: текст ответа assistant (если None —
                используется ``pagination_ctx`` как JSON).

        Returns:
            Обрезанная история (последние ``max_turns * 2`` сообщений).
        """
        import json as _json

        self.history.append({"role": "user", "content": self.user_text})
        if assistant_content is not None:
            self.history.append({"role": "assistant", "content": assistant_content})
        elif self.pagination_ctx is not None:
            self.history.append({
                "role": "assistant",
                "content": _json.dumps(self.pagination_ctx, ensure_ascii=False),
            })
        return self.history[-(max_turns * 2):]
