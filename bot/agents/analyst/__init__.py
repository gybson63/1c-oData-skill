"""Агент-аналитик метаданных 1С."""

from __future__ import annotations

__all__ = ["AnalystAgent"]


def __getattr__(name: str):
    if name == "AnalystAgent":
        from .agent_analyst import AnalystAgent

        return AnalystAgent
    raise AttributeError(name)
