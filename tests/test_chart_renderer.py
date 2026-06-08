#!/usr/bin/env python3
"""Тесты chart_renderer."""

from __future__ import annotations

import pandas as pd

from bot.agents.odata.analytics_models import ChartSpec
from bot.agents.odata.chart_renderer import render_html, render_png


def test_render_png_bar():
    df = pd.DataFrame({"Category": ["A", "B", "C"], "Amount": [10, 30, 20]})
    chart = ChartSpec(type="bar", x="Category", y="Amount", title="Test")
    png = render_png(df, chart, max_categories=10)
    assert isinstance(png, bytes)
    assert len(png) > 100
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_html_line():
    df = pd.DataFrame({"Month": ["Jan", "Feb"], "Value": [1, 2]})
    chart = ChartSpec(type="line", x="Month", y="Value", title="Trend")
    html = render_html(df, chart)
    assert "plotly" in html.lower()
    assert "Trend" in html
