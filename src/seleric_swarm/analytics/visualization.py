"""Chart spec construction.

A ``chart_spec`` is the hand-off contract between the analytics toolset and the
office UI. It is *not* a renderer-specific payload: it names a
:data:`chart_type <seleric_swarm.analytics.chart_vocabulary.CHART_TYPES>`, a
generic dataset and generic series descriptors. Both sides agree on the
vocabulary declared in :mod:`seleric_swarm.analytics.chart_vocabulary`.

No form is ever guessed from the caller's wording: a form is either requested
explicitly (and validated here) or derived from the evidence shape.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.analytics.chart_vocabulary import (
    CHART_TYPES,
    ChartType,
    normalize_chart_type,
)

SUPPORTED_CHART_TYPES: tuple[ChartType, ...] = CHART_TYPES

#: Forms drawn on a category/value plane.
CARTESIAN_CHART_TYPES: tuple[ChartType, ...] = (
    "line",
    "area",
    "bar",
    "stacked_bar",
    "grouped_bar",
    "scatter",
)

#: Forms that can still say something with a single point. Trend forms cannot.
_SINGLE_POINT_FORMS: frozenset[str] = frozenset(
    {"bar", "pie", "donut", "funnel", "radar", "heatmap"}
)


@dataclass(frozen=True)
class DataShape:
    """Plotting constraints derived from evidence — not from user wording."""

    time_periods: int
    metrics: int
    category_values: int
    values_may_be_negative: bool
    series_count: int = 1

    def describe(self) -> str:
        return (
            f"time_periods={self.time_periods}; metrics={self.metrics}; "
            f"category_values={self.category_values}; series={self.series_count}; "
            f"values_may_be_negative={str(self.values_may_be_negative).lower()}"
        )


def _series_count(evidence: list[EvidenceArtifact]) -> int:
    """How many independent marks the evidence can draw."""
    return len({
        (e.metric_id, tuple(sorted((k, v) for k, v in e.dimensions.items() if v is not None)))
        for e in evidence
    })


def describe_evidence_shape(evidence: list[EvidenceArtifact]) -> DataShape:
    time_periods = len({e.period_start for e in evidence})
    metrics = len({e.metric_id for e in evidence})
    dim_values: dict[str, set[str]] = {}
    for e in evidence:
        for k, v in e.dimensions.items():
            if v is not None:
                dim_values.setdefault(k, set()).add(v)
    category_values = max((len(v) for v in dim_values.values()), default=0)
    values_may_be_negative = any(e.value is not None and float(e.value) < 0 for e in evidence)
    return DataShape(
        time_periods=time_periods,
        metrics=metrics,
        category_values=category_values,
        values_may_be_negative=values_may_be_negative,
        series_count=_series_count(evidence),
    )


def infer_chart_type_from_shape(shape: DataShape) -> ChartType:
    """Deterministic default when no form was requested. No user-text matching.

    Multi-series evidence defaults to a stacked bar: a trend line is only an
    honest reading of a single series.
    """
    if shape.category_values > 1:
        return "stacked_bar" if shape.series_count > 1 else "bar"
    if shape.time_periods > 1:
        return "stacked_bar" if shape.series_count > 1 else "line"
    if shape.metrics > 1:
        return "funnel"
    return "bar"


def reconcile_chart_type(
    requested: str | None,
    shape: DataShape,
) -> tuple[ChartType, list[str]]:
    """Resolve the form to build, reporting every substitution.

    Nothing is remapped silently: the returned warnings travel with the spec so
    the caller — and the UI — can see that a form was changed and why.
    """
    warnings: list[str] = []
    wanted = normalize_chart_type(requested) if requested else None
    if wanted is None and requested:
        default = infer_chart_type_from_shape(shape)
        warnings.append(
            f"chart form {requested!r} is not one of {', '.join(CHART_TYPES)}; "
            f"used {default!r} instead"
        )
    resolved: ChartType = wanted or infer_chart_type_from_shape(shape)

    if resolved in ("line", "area") and shape.time_periods < 2:
        warnings.append(f"{resolved} needs at least two periods; used bar instead")
        return "bar", warnings
    has_min_two = shape.category_values >= 2 or shape.metrics >= 2
    if resolved in ("pie", "donut", "radar") and not has_min_two:
        replacement = "line" if shape.time_periods > 1 else "bar"
        warnings.append(f"{resolved} needs at least two slices or indicators; used {replacement!r} instead")
        return replacement, warnings  # type: ignore[return-value]
    if resolved == "funnel" and not has_min_two:
        replacement = "line" if shape.time_periods > 1 else "bar"
        warnings.append(f"funnel needs at least two stages; used {replacement!r} instead")
        return replacement, warnings  # type: ignore[return-value]
    return resolved, warnings


def _format_time_label(dt: Any) -> str:
    if hasattr(dt, "strftime"):
        return dt.strftime("%Y-%m-%d")
    return str(dt)[:10]


def _format_metric_label(metric_id: str) -> str:
    return metric_id.removeprefix("metric.").replace("_", " ").title()


def _dims_of(item: EvidenceArtifact) -> dict[str, str]:
    return {k: str(v) for k, v in sorted(item.dimensions.items()) if v is not None}


def _varying_dimension_keys(evidence: list[EvidenceArtifact]) -> list[str]:
    distinct: dict[str, set[Any]] = {}
    for e in evidence:
        for k, v in e.dimensions.items():
            if v is not None:
                distinct.setdefault(k, set()).add(v)
    return [k for k, vals in sorted(distinct.items()) if len(vals) > 1]


def _dimension_key(item: EvidenceArtifact, keys: list[str]) -> str:
    parts = [str(item.dimensions[k]) for k in keys if item.dimensions.get(k) is not None]
    return " - ".join(parts) if parts else "Total"


def _group_by_dimension(evidence: list[EvidenceArtifact], dimension_keys: list[str]) -> dict[str, float]:
    """Group data by dimension for composition and category forms."""
    result: dict[str, float] = defaultdict(float)
    for item in evidence:
        if dimension_keys and item.dimensions:
            key = _dimension_key(item, dimension_keys)
        elif item.dimensions:
            key = " - ".join(str(v) for _, v in sorted(item.dimensions.items()) if v is not None)
        else:
            key = _format_metric_label(item.metric_id)
        if item.value is not None:
            result[key] += float(item.value)
    return dict(result)


def _group_by_time_multi(
    evidence: list[EvidenceArtifact],
    key_builder: Callable[[EvidenceArtifact], str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Group time series data by time string and dynamic series keys."""
    time_series: dict[str, dict[str, float]] = defaultdict(dict)
    all_keys: set[str] = set()
    for item in evidence:
        time_str = _format_time_label(item.period_start)
        s_key = key_builder(item)
        all_keys.add(s_key)
        if item.value is not None:
            time_series[time_str][s_key] = float(item.value)

    rows: list[dict[str, Any]] = []
    for t_str in sorted(time_series.keys()):
        row: dict[str, Any] = {"time": t_str}
        row.update(time_series[t_str])
        rows.append(row)
    return rows, sorted(all_keys)


def _mark_for(chart_type: ChartType) -> str:
    return "bar" if chart_type in ("bar", "stacked_bar", "grouped_bar") else "line"


# --------------------------------------------------------------------------
# Builders. One per chart form; each fills ``series``/``data``/axes in the spec.
# --------------------------------------------------------------------------


def _build_scatter(
    spec: dict[str, Any],
    evidence: list[EvidenceArtifact],
    metrics: list[str],
    varying_dim_keys: list[str],
) -> None:
    """Scatter plots two measures against each other.

    With two or more metrics the pair is (first metric, second metric). With a
    single metric the period index is the x value so every observation is still
    a point.
    """
    y_metric = metrics[1] if len(metrics) > 1 else (metrics[0] if metrics else None)
    x_metric = metrics[0] if len(metrics) > 1 else None
    periods = sorted({e.period_start for e in evidence})
    period_index = {p: i for i, p in enumerate(periods)}

    # One point per slice, never one per metric row: the pair is read off the
    # slice, not emitted per measure.
    rows: list[dict[str, Any]] = []
    for item in evidence:
        if item.value is None or (x_metric is not None and item.metric_id != x_metric):
            continue
        stage = (
            _dimension_key(item, varying_dim_keys)
            if varying_dim_keys
            else _format_time_label(item.period_start)
        )
        y_value = (
            _co_metric_value(evidence, y_metric, item)
            if x_metric is not None and y_metric is not None
            else float(item.value)
        )
        if y_value is None:
            continue
        x_value = (
            _co_metric_value(evidence, x_metric, item) if x_metric is not None
            else float(period_index[item.period_start])
        )
        rows.append({"x": x_value, "y": y_value, "name": stage})

    spec["xAxis"] = {
        "type": "value",
        "key": "x",
        "name": _format_metric_label(x_metric) if x_metric else "period",
    }
    spec["yAxis"] = [{
        "type": "value",
        "name": _format_metric_label(y_metric) if y_metric else "value",
        "position": "left",
    }]
    spec["data"] = rows
    spec["series"].append({
        "key": "y",
        "name": _format_metric_label(y_metric) if y_metric else "value",
        "type": "scatter",
        "yAxisIndex": 0,
    })


def _co_metric_value(
    evidence: list[EvidenceArtifact], metric_id: str, item: EvidenceArtifact
) -> float | None:
    """Value of ``metric_id`` on the same period/dimension slice as ``item``."""
    dims = _dims_of(item)
    for other in evidence:
        if other.metric_id != metric_id or other.period_start != item.period_start:
            continue
        if _dims_of(other) != dims:
            continue
        return float(other.value) if other.value is not None else None
    return None


def _build_cartesian(
    spec: dict[str, Any],
    evidence: list[EvidenceArtifact],
    metrics: list[str],
    varying_dim_keys: list[str],
    time_labels: list[str],
) -> None:
    """Line / area / bar / stacked bar / grouped bar over periods or categories."""
    chart_type: ChartType = spec["chart_type"]
    if chart_type == "scatter":
        _build_scatter(spec, evidence, metrics, varying_dim_keys)
        return

    units = {e.metric_id: e.unit or "val" for e in evidence}
    unique_units = list(dict.fromkeys(units.values()))
    has_multi_metrics = len(metrics) > 1

    def build_key(item: EvidenceArtifact) -> str:
        label = _format_metric_label(item.metric_id)
        if varying_dim_keys and item.dimensions:
            dim_str = _dimension_key(item, varying_dim_keys)
            if dim_str:
                return f"{label} ({dim_str})" if has_multi_metrics else dim_str
        return label

    x_key = "time" if time_labels else "name"

    if time_labels:
        data_rows, series_names = _group_by_time_multi(evidence, build_key)
    else:
        if has_multi_metrics and varying_dim_keys:
            series_names = [_format_metric_label(m) for m in metrics]
            categories = sorted(_group_by_dimension(evidence, varying_dim_keys))
            data_rows = [{"name": cat, **{s_name: 0.0 for s_name in series_names}} for cat in categories]
            for item in evidence:
                if item.value is None:
                    continue
                row = next(
                    (r for r in data_rows if r["name"] == _dimension_key(item, varying_dim_keys)),
                    None,
                )
                if row is not None:
                    row[_format_metric_label(item.metric_id)] = float(item.value)
        else:
            grouped = _group_by_dimension(evidence, varying_dim_keys)
            series_names = ["value"]
            data_rows = [{"name": k, "value": v} for k, v in grouped.items()]

    spec["xAxis"] = {"type": "category", "key": x_key}
    spec["data"] = data_rows

    stack = "total" if chart_type == "stacked_bar" and len(series_names) > 1 else None
    for s_name in series_names:
        matched_metric = next(
            (m for m in metrics if _format_metric_label(m) in s_name or m in s_name),
            metrics[0] if metrics else "",
        )
        entry: dict[str, Any] = {
            "key": s_name,
            "name": s_name,
            "type": _mark_for(chart_type),
            "yAxisIndex": unique_units.index(units[matched_metric]) if matched_metric in units else 0,
        }
        if chart_type == "area":
            entry["areaStyle"] = {"opacity": 0.25}
        if stack:
            entry["stack"] = stack
        spec["series"].append(entry)

    # A single derived series ranks best sorted; a real multi-series set is
    # already ordered by the x axis and must not be reordered.
    if len(series_names) == 1 and x_key == "name" and spec["series"] and spec["series"][0]["key"] == "value":
        spec["data"] = sorted(spec["data"], key=lambda r: r.get("value", 0), reverse=True)
        if len(spec["data"]) > 15:
            spec["data"] = spec["data"][:15]
            spec["title"] += " (Top 15)"


def _build_composition(
    spec: dict[str, Any],
    evidence: list[EvidenceArtifact],
    metrics: list[str],
    varying_dim_keys: list[str],
) -> None:
    """Pie / donut / funnel: one slice per category."""
    chart_type: ChartType = spec["chart_type"]
    grouped = _group_by_dimension(evidence, varying_dim_keys) or {
        _format_metric_label(m): 0.0 for m in metrics
    }
    rows: list[tuple[str, float]] = sorted(grouped.items(), key=lambda kv: kv[1], reverse=True)
    data: list[dict[str, Any]] = [{"name": k, "value": v} for k, v in rows]
    if chart_type in ("pie", "donut") and len(data) > 7:
        head = data[:6]
        head.append({"name": "Other", "value": sum(v for _, v in rows[6:])})
        data = head

    main_metric = _format_metric_label(metrics[0]) if metrics else "Value"
    series: dict[str, Any] = {"key": "value", "name": main_metric, "type": chart_type}
    if chart_type == "donut":
        series["radius"] = ["40%", "70%"]
    spec["data"] = data
    spec["series"].append(series)


def _build_radar(
    spec: dict[str, Any],
    evidence: list[EvidenceArtifact],
    varying_dim_keys: list[str],
) -> None:
    """One indicator per metric, one series per dimension group."""
    metrics = list(dict.fromkeys(e.metric_id for e in evidence))
    groups: dict[str, dict[str, float]] = defaultdict(dict)
    for e in evidence:
        if e.value is None:
            continue
        key = _dimension_key(e, varying_dim_keys) if varying_dim_keys else "Series"
        groups[key][e.metric_id] = float(e.value)

    indicators = [
        {
            "name": _format_metric_label(m),
            "max": max((abs(g[m]) for g in groups.values() if m in g), default=1.0) * 1.1,
        }
        for m in metrics
    ]
    series_keys = sorted(groups)
    spec["radar"] = {"indicator": indicators}
    spec["data"] = [
        dict({"indicator": ind["name"]}, **{key: groups[key].get(m, 0.0) for key in series_keys})
        for ind, m in zip(indicators, metrics)
    ]
    for key in series_keys:
        spec["series"].append({"key": key, "name": key, "type": "radar"})


def _build_heatmap(
    spec: dict[str, Any],
    evidence: list[EvidenceArtifact],
    metrics: list[str],
    varying_dim_keys: list[str],
) -> None:
    """Magnitude of each metric per period (or per category)."""
    periods = sorted({e.period_start for e in evidence})
    across_periods = len(periods) > 1
    category_of = (
        (lambda e: _dimension_key(e, varying_dim_keys))
        if varying_dim_keys
        else (lambda e: _format_metric_label(e.metric_id))
    )

    x_labels = (
        [_format_time_label(p) for p in periods]
        if across_periods
        else sorted({category_of(e) for e in evidence})
    )
    y_labels = sorted({_format_metric_label(e.metric_id) for e in evidence})

    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for e in evidence:
        if e.value is None:
            continue
        key = (
            _format_time_label(e.period_start) if across_periods else category_of(e),
            _format_metric_label(e.metric_id),
        )
        row = rows.setdefault(key, {"x": key[0], "y": key[1], "value": 0.0})
        row["value"] += float(e.value)
    data = sorted(rows.values(), key=lambda r: (r["y"], r["x"]))

    spec["xAxis"] = {"type": "category", "key": "x", "data": x_labels}
    spec["yAxis"] = [{"type": "category", "key": "y", "data": y_labels}]
    spec["data"] = data
    spec["series"].append({
        "key": "value",
        "name": _format_metric_label(metrics[0]) if metrics else "Value",
        "type": "heatmap",
        "yAxisIndex": 0,
    })


def generate_visualization_spec(
    evidence: list[EvidenceArtifact],
    intent: str = "",
    title: str = "Visualization",
    *,
    chart_type: ChartType | str | None = None,
) -> dict[str, Any]:
    """Build a generic visualization specification from validated evidence.

    ``chart_type`` is the only thing that selects the form; ``intent`` is
    accepted for call-site compatibility and is never inspected to pick a form.
    An unrecognized ``chart_type`` is an error, not a silent substitution.
    """
    _ = intent
    if not evidence:
        return {"error": "No evidence provided."}

    shape = describe_evidence_shape(evidence)
    requested = normalize_chart_type(chart_type) if chart_type else None
    if chart_type and requested is None:
        return {
            "error": (
                f"Unsupported chart_type {chart_type!r}. Supported forms: "
                f"{', '.join(CHART_TYPES)}."
            )
        }

    # Only an explicitly requested form can draw from a single point; a
    # trend form over one period says nothing.
    if len(evidence) < 2 and requested not in _SINGLE_POINT_FORMS:
        return {"error": "Insufficient data points for visualization. Need at least 2 data points."}

    resolved, warnings = reconcile_chart_type(requested, shape)

    metrics = list(dict.fromkeys(e.metric_id for e in evidence))
    varying_dim_keys = _varying_dimension_keys(evidence)
    all_dims = list(dict.fromkeys(k for e in evidence for k in e.dimensions))
    units = {e.metric_id: e.unit or "val" for e in evidence}
    unique_units = list(dict.fromkeys(units.values()))

    spec: dict[str, Any] = {
        "title": title,
        "chart_type": resolved,
        "metrics": metrics,
        "dimensions": varying_dim_keys or all_dims,
        "series": [],
        "xAxis": {},
        "yAxis": [],
        "data": [],
    }
    if warnings:
        spec["warnings"] = warnings

    for idx, u in enumerate(unique_units):
        spec["yAxis"].append({
            "type": "value",
            "name": u,
            "position": "left" if idx == 0 else "right",
        })

    if resolved in CARTESIAN_CHART_TYPES:
        periods = sorted({e.period_start for e in evidence})
        time_labels = [_format_time_label(p) for p in periods] if len(periods) > 1 else []
        _build_cartesian(spec, evidence, metrics, varying_dim_keys, time_labels)
    elif resolved in ("pie", "donut", "funnel"):
        _build_composition(spec, evidence, metrics, varying_dim_keys)
    elif resolved == "radar":
        _build_radar(spec, evidence, varying_dim_keys)
    elif resolved == "heatmap":
        _build_heatmap(spec, evidence, metrics, varying_dim_keys)
    else:  # pragma: no cover - every declared form has a builder above
        return {"error": f"Chart form {resolved!r} has no builder."}

    return spec
