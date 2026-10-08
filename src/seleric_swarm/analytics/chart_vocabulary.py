"""Canonical chart vocabulary — the single source of truth for chart forms.

This module is the contract between the analytics toolset (which builds a
``chart_spec`` artifact), the API adapters (which forward it) and the office UI
(which renders it). Every supported form is declared here exactly once; both
sides validate against it instead of keeping their own copy.

Adding a form means adding it here, giving it a builder in
:mod:`seleric_swarm.analytics.visualization` and a renderer in the office UI.
"""

from __future__ import annotations

import re
from typing import Any, Literal

ChartType = Literal[
    "line",
    "area",
    "bar",
    "stacked_bar",
    "grouped_bar",
    "pie",
    "donut",
    "funnel",
    "scatter",
    "radar",
    "heatmap",
]

#: Every form the spec builder can emit, in render order.
CHART_TYPES: tuple[ChartType, ...] = (
    "line",
    "area",
    "bar",
    "stacked_bar",
    "grouped_bar",
    "pie",
    "donut",
    "funnel",
    "scatter",
    "radar",
    "heatmap",
)

SUPPORTED_CHART_TYPES: tuple[ChartType, ...] = CHART_TYPES

# Forms that share the cartesian (x/y) dataset shape. Series of these forms are
# independent marks: ``stacked_bar`` adds an ECharts ``stack`` to each series.
CARTESIAN_CHART_TYPES: tuple[ChartType, ...] = (
    "line",
    "area",
    "bar",
    "stacked_bar",
    "grouped_bar",
    "scatter",
)


def _key(value: str) -> str:
    """Normalize a user/LLM spelling to a comparable token."""
    return "_".join(
        part
        for part in re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).split("_")
        if part
    )


# Spellings accepted from tool calls and from callers. Keys are normalized with
# :func:`_key`, so "Stacked Bar", "stacked-bar", "stackedBar" and "stacked_bar"
# all land here. The canonical names are always accepted as-is.
CHART_TYPE_ALIASES: dict[str, ChartType] = {
    alias: chart_type
    for chart_type in CHART_TYPES
    for alias in (chart_type, f"{chart_type}_chart".replace("__", "_"), f"{chart_type}s")
    if alias
} | {
    # bars
    "stacked": "stacked_bar",
    "stack": "stacked_bar",
    "stackedbar": "stacked_bar",
    "stacked_column": "stacked_bar",
    "stackedcolumn": "stacked_bar",
    "column": "bar",
    "columns": "bar",
    "column_chart": "bar",
    "vertical_bar": "bar",
    "grouped": "grouped_bar",
    "group": "grouped_bar",
    "groupedbar": "grouped_bar",
    "clustered": "grouped_bar",
    "clustered_bar": "grouped_bar",
    "multi_bar": "grouped_bar",
    "bar_chart": "bar",
    "horizontal_bar": "bar",
    "bar_over_time": "bar",
    # time series
    "trend": "line",
    "trendline": "line",
    "time_series": "line",
    "line_chart": "line",
    "area_chart": "area",
    "timeseries": "line",
    # composition
    "pie_chart": "pie",
    "donut_chart": "donut",
    "doughnut": "donut",
    "composition": "pie",
    "share": "pie",
    "shares": "pie",
    # stage drop-off
    "funnel_chart": "funnel",
    "stages": "funnel",
    "conversion_funnel": "funnel",
    # correlation
    "scatter_plot": "scatter",
    "scatterplot": "scatter",
    "bubble": "scatter",
    "bubble_chart": "scatter",
    "correlation": "scatter",
    # multivariate
    "radar_chart": "radar",
    "spider": "radar",
    "spider_chart": "radar",
    "kiviat": "radar",
    # density
    "heat_map": "heatmap",
    "heatmap_chart": "heatmap",
    "density": "heatmap",
    "matrix": "heatmap",
}

_CHART_TYPE_METADATA: dict[str, dict[str, Any]] = {
    "line": {
        "label": "Line",
        "family": "cartesian",
        "description": "Continuous change over time for one or more series.",
        "axes": "category x, value y",
        "stacking": "none",
    },
    "area": {
        "label": "Area",
        "family": "cartesian",
        "description": "Continuous change over time with filled magnitude.",
        "axes": "category x, value y",
        "stacking": "none",
    },
    "bar": {
        "label": "Bar",
        "family": "cartesian",
        "description": "Discrete comparison by period or category.",
        "axes": "category x, value y",
        "stacking": "none",
    },
    "stacked_bar": {
        "label": "Stacked bar",
        "family": "cartesian",
        "description": "Part-to-whole contribution of several series per period or category.",
        "axes": "category x, value y",
        "stacking": "total",
    },
    "grouped_bar": {
        "label": "Grouped bar",
        "family": "cartesian",
        "description": "Side-by-side bars comparing several series per period or category.",
        "axes": "category x, value y",
        "stacking": "none",
    },
    "pie": {
        "label": "Pie",
        "family": "composition",
        "description": "Shares of a whole across categories.",
        "axes": "category slices",
        "stacking": "none",
    },
    "donut": {
        "label": "Donut",
        "family": "composition",
        "description": "Shares of a whole with a hollow centre.",
        "axes": "category slices",
        "stacking": "none",
    },
    "funnel": {
        "label": "Funnel",
        "family": "composition",
        "description": "Sequential stage drop-off.",
        "axes": "ordered stages",
        "stacking": "none",
    },
    "scatter": {
        "label": "Scatter",
        "family": "cartesian",
        "description": "Relationship between two measures over time or category.",
        "axes": "value x, value y",
        "stacking": "none",
    },
    "radar": {
        "label": "Radar",
        "family": "composition",
        "description": "Several measures compared on a common radial scale.",
        "axes": "radial indicators",
        "stacking": "none",
    },
    "heatmap": {
        "label": "Heatmap",
        "family": "matrix",
        "description": "Magnitude at the intersection of two dimensions.",
        "axes": "category x, category y",
        "stacking": "none",
    },
}


def normalize_chart_type(raw: Any) -> ChartType | None:
    """Map any accepted spelling to a canonical chart form.

    Returns ``None`` when the spelling is not a supported form — callers must
    treat that as an error, never silently fall back to another form.
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if text in CHART_TYPES:
        return text  # type: ignore[return-value]
    alias = CHART_TYPE_ALIASES.get(_key(text))
    if alias is not None:
        return alias
    return CHART_TYPE_ALIASES.get(text.strip().lower())  # type: ignore[return-value]


def chart_type_catalog() -> list[dict[str, Any]]:
    """Machine-readable vocabulary, published to the UI by the office gateway.

    The UI validates every incoming spec against this list, so a backend that
    grows a form the UI cannot render fails loudly instead of blanking.
    """
    catalog: list[dict[str, Any]] = []
    for chart_type in CHART_TYPES:
        meta = _CHART_TYPE_METADATA[chart_type]
        catalog.append(
            {
                "chart_type": chart_type,
                "label": meta["label"],
                "description": meta["description"],
                "family": meta["family"],
                "stacking": meta["stacking"],
                "axes": meta["axes"],
                "aliases": sorted(
                    alias
                    for alias, target in CHART_TYPE_ALIASES.items()
                    if target == chart_type and alias != chart_type
                ),
            }
        )
    return catalog


def chart_type_summary() -> str:
    """Human-readable list for tool summaries and refusal messages."""
    return ", ".join(f"{chart_type} ({meta['label']})" for chart_type, meta in _CHART_TYPE_METADATA.items())
