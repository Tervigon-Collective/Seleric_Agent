"""Forecast fan chart as a standard ``chart_spec`` (same widget as every other chart)."""

from __future__ import annotations

from datetime import date
from typing import Any

from seleric_swarm.forecasting.types import DailyPoint

_HISTORY_DAYS = 28


def _label(metric_id: str) -> str:
    return metric_id.removeprefix("metric.").replace("_", " ").title()


def build_forecast_chart_spec(
    *,
    metric_id: str,
    label: str | None,
    unit: str | None,
    history: list[float | None],
    history_dates: list[date],
    days: list[DailyPoint],
    history_days: int = _HISTORY_DAYS,
) -> dict[str, Any]:
    """Recent actuals + median path + 80% band as a line chart over one time axis."""
    name = label or _label(metric_id)
    rows: dict[str, dict[str, Any]] = {}
    for d, v in list(zip(history_dates, history, strict=False))[-history_days:]:
        if v is None:
            continue
        rows.setdefault(d.isoformat(), {"time": d.isoformat()})["Actual"] = float(v)
    for pt in days:
        row = rows.setdefault(pt.date[:10], {"time": pt.date[:10]})
        row["Forecast"] = pt.p50
        row["Lower (P10)"] = pt.p10
        row["Range width (P90 - P10)"] = max(0.0, pt.p90 - pt.p10)
    data = [rows[k] for k in sorted(rows)]
    series: list[dict[str, Any]] = [
        {"key": "Actual", "name": "Actual", "type": "line", "yAxisIndex": 0},
        {"key": "Forecast", "name": "Forecast (P50)", "type": "line", "yAxisIndex": 0},
        # The 80% band is drawn as a shaded ribbon: an invisible P10 base with the
        # P10-to-P90 width stacked on top. The top edge of the ribbon is P90.
        {
            "key": "Lower (P10)", "name": "Lower (P10)", "type": "line", "yAxisIndex": 0,
            "stack": "band", "areaStyle": {"opacity": 0},
        },
        {
            "key": "Range width (P90 - P10)", "name": "Range width (P90 - P10)",
            "type": "line", "yAxisIndex": 0, "stack": "band", "areaStyle": {"opacity": 0.22},
        },
    ]
    return {
        "title": f"{name}: forecast with 80% range",
        "chart_type": "line",
        "metrics": [metric_id],
        "dimensions": [],
        "series": series,
        "xAxis": {"type": "category", "key": "time"},
        "yAxis": [{"type": "value", "name": unit or "val", "position": "left"}],
        "data": data,
    }
