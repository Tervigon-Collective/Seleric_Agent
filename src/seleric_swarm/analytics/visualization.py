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
from contextvars import ContextVar
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
    "horizontal_bar",
    "stacked_bar",
    "grouped_bar",
    "combo",
    "scatter",
)

#: Forms that can still say something with a single point. Trend forms cannot.
_SINGLE_POINT_FORMS: frozenset[str] = frozenset(
    {"bar", "horizontal_bar", "pie", "donut", "treemap", "funnel", "waterfall", "radar", "heatmap"}
)

#: Series of a different size or kind are drawn in their own panel past this ratio of typical magnitudes
#: (net sales ~290k beside AOV ~1.9k: one shared axis flattens the small one into an invisible sliver).
_PANEL_SCALE_RATIO = 30.0
_MAX_PANELS = 4

# Caps for a readable cartesian chart. Beyond these the legend and dual axes
# drown the question (live 2026-10-11 MS3-5637552cae: ad_spend×clicks×cvr×roas
# across ~12 campaigns → 40+ series on one grouped_bar). Prefer one metric and
# the top entities by magnitude; never invent a second chart from user wording.
_MAX_CHART_SERIES = 12
_MAX_CHART_CATEGORIES = 8
#: One measure ranked across entities reads fine at this length (a horizontal bar per name); several series do not.
_MAX_RANKING_CATEGORIES = 15

#: Forms that add series values together (visually or semantically). Every
#: stacked segment must share one unit group — see ``infer_unit_group``.
#: Composition forms (pie/donut/funnel) likewise require a single group:
#: shares of a whole are meaningless across currencies and counts.
STACKING_FORMS: frozenset[str] = frozenset({"stacked_bar"})
COMPOSITION_FORMS: frozenset[str] = frozenset({"pie", "donut", "treemap", "funnel"})
#: Composition forms that cannot draw a negative share.
_NON_NEGATIVE_FORMS: frozenset[str] = frozenset({"pie", "donut", "treemap"})

_CURRENCY_UNITS = frozenset({
    "inr", "rs", "₹", "rupee", "rupees", "usd", "$", "eur", "€", "gbp", "£", "currency",
})
_COUNT_UNITS = frozenset({
    "count", "counts", "orders", "sessions", "units", "qty", "quantity",
    "impressions", "clicks", "views", "visitors", "users", "val_count",
})
_RATIO_UNITS = frozenset({"pct", "percent", "%", "ratio", "rate", "x"})


def infer_unit_group(metric_id: str, unit: str | None) -> str:
    """Map a metric to a stack-compatibility group from its catalogue unit alone.

    Nothing is read from the metric's name: the catalogue says whether a measure is money, a count or a ratio, and a
    unit it does not describe is its own group (``unit:<x>``) or ``unknown``."""
    _ = metric_id
    raw = (unit or "").strip()
    u = raw.lower().rstrip("s")
    if u in _CURRENCY_UNITS or (len(raw) == 3 and raw.isalpha() and raw.isupper()):
        return "currency"
    if u in _COUNT_UNITS:
        return "count"
    if u in _RATIO_UNITS:
        return "ratio"
    if u and u != "val":
        return f"unit:{u}"
    return "unknown"


def unit_groups_for_evidence(
    evidence: list[EvidenceArtifact], aggregations: dict[str, str] | None = None,
) -> dict[str, str]:
    """Metric id -> unit group for every metric in the evidence set.

    The catalogue's own ``aggregation`` is the authority on a ratio (an average is never summed and never shares a
    volume's axis); the unit covers a metric whose aggregation the catalogue does not carry."""
    aggregations = aggregations or {}
    return {
        m: "ratio" if aggregations.get(m) == "ratio" else infer_unit_group(
            m, next((e.unit for e in evidence if e.metric_id == m and e.unit), None))
        for m in dict.fromkeys(e.metric_id for e in evidence)
    }


def _known_groups(groups: dict[str, str]) -> list[str]:
    return sorted({g for g in groups.values() if g != "unknown"})


def stacking_allowed(chart_type: str, groups: dict[str, str] | None) -> bool:
    """Whether ``chart_type`` may add series values together.

    Stacked and composition forms require a single known unit group, and that
    group must not be a ratio — summing rates/ratios is never meaningful.
    ``None``/empty/unknown-only groups allow (nothing disproves compatibility).
    """
    if chart_type not in STACKING_FORMS | COMPOSITION_FORMS:
        return True
    if not groups:
        return True
    known = _known_groups(groups)
    if len(known) > 1:
        return False
    return "ratio" not in known


def stacking_warning(chart_type: str, groups: dict[str, str]) -> str:
    known = _known_groups(groups) or ["unknown"]
    return (
        f"{chart_type} needs series with matching units "
        f"(found: {', '.join(known)}); used grouped_bar instead so values "
        f"are compared side-by-side rather than summed"
    )


@dataclass(frozen=True)
class DataShape:
    """Plotting constraints derived from evidence — not from user wording."""

    time_periods: int
    metrics: int
    category_values: int
    values_may_be_negative: bool
    series_count: int = 1
    longest_label: int = 0

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
    longest_label = max((len(str(v)) for vals in dim_values.values() for v in vals), default=0)
    return DataShape(
        time_periods=time_periods,
        metrics=metrics,
        category_values=category_values,
        values_may_be_negative=values_may_be_negative,
        series_count=_series_count(evidence),
        longest_label=longest_label,
    )


_LONG_LABEL = 14  # characters: a longer name is rotated and clipped under a vertical bar


def infer_chart_type_from_shape(
    shape: DataShape,
    unit_groups: dict[str, str] | None = None,
) -> ChartType:
    """Deterministic default when no form was requested. No user-text matching.

    * entities, one measure -> a ranking: horizontal bars (names stay readable), plain bars for a handful;
    * a measure over time -> a line (several measures over time are lines in their own panels, never stacked:
      different things do not add up); bars and a line on two axes only for exactly one volume + one rate;
    * one measure split by entity over time -> stacked bars while the parts add up and stay few, else lines;
    * several measures per entity -> side-by-side bars, or a scatter when two unlike measures meet many entities.
    """
    known = _known_groups(unit_groups) if unit_groups else []
    additive = unit_groups is None or stacking_allowed("stacked_bar", unit_groups)
    if shape.category_values > 1:
        if shape.time_periods > 1:
            if shape.metrics > 1:
                return "grouped_bar"
            return "stacked_bar" if additive and shape.category_values <= 6 else "line"
        if shape.metrics > 1:
            if len(known) > 1 and shape.metrics == 2 and shape.category_values >= 5:
                return "scatter"
            return "grouped_bar"
        return "horizontal_bar" if shape.category_values >= 4 or shape.longest_label > _LONG_LABEL else "bar"
    if shape.time_periods > 1:
        if shape.metrics == 2 and len(known) == 2 and shape.time_periods >= 3:
            return "combo"
        return "line"
    if shape.metrics > 1:
        return "grouped_bar"
    return "bar"


def reconcile_chart_type(
    requested: str | None,
    shape: DataShape,
    unit_groups: dict[str, str] | None = None,
) -> tuple[ChartType, list[str]]:
    """Resolve the form to build, reporting every substitution.

    Nothing is remapped silently: the returned warnings travel with the spec so
    the caller — and the UI — can see that a form was changed and why.
    Unit-incompatible stacks are downgraded to grouped bars here, and
    :func:`generate_visualization_spec` passes the evidence unit groups in.
    """
    warnings: list[str] = []
    wanted = normalize_chart_type(requested) if requested else None
    if wanted is None and requested:
        default = infer_chart_type_from_shape(shape, unit_groups)
        warnings.append(
            f"chart form {requested!r} is not one of {', '.join(CHART_TYPES)}; "
            f"used {default!r} instead"
        )
    resolved: ChartType = wanted or infer_chart_type_from_shape(shape, unit_groups)

    if resolved in STACKING_FORMS | COMPOSITION_FORMS and not stacking_allowed(
        resolved, unit_groups
    ):
        warnings.append(stacking_warning(resolved, unit_groups or {}))
        return "grouped_bar", warnings
    if resolved in _NON_NEGATIVE_FORMS and shape.values_may_be_negative:
        warnings.append(f"{resolved} cannot draw negative values; used horizontal_bar instead")
        return "horizontal_bar", warnings
    if resolved == "horizontal_bar" and shape.category_values < 2:
        warnings.append("horizontal_bar needs at least two categories; used bar instead")
        return "bar", warnings
    if resolved == "combo":
        known = _known_groups(unit_groups) if unit_groups else []
        if shape.time_periods < 2 or shape.metrics < 2 or (unit_groups is not None and len(known) != 2):
            replacement = "line" if shape.time_periods > 1 else "grouped_bar"
            warnings.append(
                "combo needs one volume and one rate over at least two periods; "
                f"used {replacement!r} instead"
            )
            return replacement, warnings  # type: ignore[return-value]
    if resolved == "waterfall":
        steps = shape.category_values if shape.category_values > 1 else shape.metrics
        if steps < 2 or shape.time_periods > 1:
            replacement = "line" if shape.time_periods > 1 else "bar"
            warnings.append(
                "waterfall needs two or more signed steps within one period; "
                f"used {replacement!r} instead"
            )
            return replacement, warnings  # type: ignore[return-value]

    if resolved in ("line", "area") and shape.time_periods < 2:
        warnings.append(f"{resolved} needs at least two periods; used bar instead")
        return "bar", warnings
    has_min_two = shape.category_values >= 2 or shape.metrics >= 2
    if resolved in ("pie", "donut", "treemap", "radar") and not has_min_two:
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


_LABELS: ContextVar[dict[str, str] | None] = ContextVar("chart_labels", default=None)


def _format_metric_label(metric_id: str) -> str:
    """The catalogue's name for a metric when the caller supplied it, else its id made readable."""
    known = (_LABELS.get() or {}).get(metric_id)
    return known or metric_id.removeprefix("metric.").replace("_", " ").title()


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
    return "bar" if chart_type in ("bar", "horizontal_bar", "stacked_bar", "grouped_bar", "combo") else "line"


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
    unit_groups: dict[str, str] | None = None,
) -> None:
    """Line / area / bar / horizontal bar / stacked / grouped bar / combo over periods or categories."""
    chart_type: ChartType = spec["chart_type"]
    if chart_type == "scatter":
        _build_scatter(spec, evidence, metrics, varying_dim_keys)
        return

    units = {e.metric_id: e.unit or "val" for e in evidence}
    has_multi_metrics = len(metrics) > 1
    series_metric: dict[str, str] = {}

    def build_key(item: EvidenceArtifact) -> str:
        label = _format_metric_label(item.metric_id)
        key = label
        if varying_dim_keys and item.dimensions:
            dim_str = _dimension_key(item, varying_dim_keys)
            if dim_str:
                key = f"{label} ({dim_str})" if has_multi_metrics else dim_str
        series_metric.setdefault(key, item.metric_id)
        return key

    x_key = "time" if time_labels else "name"

    if time_labels:
        data_rows, series_names = _group_by_time_multi(evidence, build_key)
    else:
        if has_multi_metrics and varying_dim_keys:
            series_names = [_format_metric_label(m) for m in metrics]
            series_metric.update({_format_metric_label(m): m for m in metrics})
            categories = sorted(_group_by_dimension(evidence, varying_dim_keys))
            # A cell the data does not have stays empty: a missing value is unknown, never zero.
            data_rows = [{"name": cat, **{s_name: None for s_name in series_names}} for cat in categories]
            by_name = {r["name"]: r for r in data_rows}
            for item in evidence:
                row = by_name.get(_dimension_key(item, varying_dim_keys))
                if item.value is not None and row is not None:
                    row[_format_metric_label(item.metric_id)] = float(item.value)
        elif has_multi_metrics:
            # one period, no entity: the metrics themselves are the categories
            series_names = ["value"]
            series_metric["value"] = metrics[0]
            by_metric = {m: next((float(e.value) for e in evidence if e.metric_id == m and e.value is not None), None)
                         for m in metrics}
            data_rows = [{"name": _format_metric_label(m), "value": v} for m, v in by_metric.items()]
        else:
            grouped = _group_by_dimension(evidence, varying_dim_keys)
            series_names = ["value"]
            series_metric["value"] = metrics[0] if metrics else ""
            data_rows = [{"name": k, "value": v} for k, v in grouped.items()]

    spec["xAxis"] = {"type": "category", "key": x_key}
    spec["data"] = data_rows

    groups = unit_groups or {m: infer_unit_group(m, units.get(m)) for m in metrics}
    can_stack = stacking_allowed(chart_type, groups)
    stack = "total" if chart_type == "stacked_bar" and len(series_names) > 1 and can_stack else None
    # A combo draws the first volume as bars and everything else as lines on the other axis.
    bar_group = next((groups[m] for m in metrics if groups.get(m) != "ratio"), groups.get(metrics[0]) if metrics else "")
    axis_groups = list(dict.fromkeys(groups.get(series_metric.get(n, ""), "") for n in series_names))
    if chart_type == "combo":
        axis_groups = sorted(axis_groups, key=lambda g: g != bar_group)
    axis_units: list[str] = []
    for s_name in series_names:
        metric_id = series_metric.get(s_name, metrics[0] if metrics else "")
        group = groups.get(metric_id, "")
        mark = _mark_for(chart_type)
        axis_index = 0
        if chart_type == "combo":
            mark = "bar" if group == bar_group else "line"
            axis_index = axis_groups.index(group)
        else:
            unit = units.get(metric_id, "val")
            if unit not in axis_units:
                axis_units.append(unit)
            axis_index = axis_units.index(unit)
        entry: dict[str, Any] = {
            "key": s_name,
            "name": s_name,
            "metric": metric_id,
            "type": mark,
            "yAxisIndex": axis_index,
        }
        if chart_type == "area":
            entry["areaStyle"] = {"opacity": 0.25}
        if stack:
            entry["stack"] = stack
        spec["series"].append(entry)
    if chart_type == "combo":
        spec["yAxis"] = [
            {"type": "value", "name": next((units.get(m, "val") for m in metrics if groups.get(m) == g), g),
             "position": "left" if i == 0 else "right"}
            for i, g in enumerate(axis_groups)
        ]
    elif axis_units:
        spec["yAxis"] = [
            {"type": "value", "name": u, "position": "left" if i == 0 else "right"} for i, u in enumerate(axis_units)
        ]

    # A single derived series ranks best sorted; a real multi-series set is
    # already ordered by the x axis and must not be reordered.
    if len(series_names) == 1 and x_key == "name" and spec["series"] and spec["series"][0]["key"] == "value":
        spec["data"] = sorted(spec["data"], key=lambda r: r.get("value") or 0, reverse=True)
        limit = 15 if chart_type == "horizontal_bar" else 12
        if len(spec["data"]) > limit:
            spec["data"] = spec["data"][:limit]
            spec["title"] += f" (Top {limit})"


def _metric_scale(evidence: list[EvidenceArtifact], metric_id: str) -> float:
    """Typical magnitude of a metric: the median of its non-zero absolute values."""
    values = sorted(abs(float(e.value)) for e in evidence if e.metric_id == metric_id and e.value)
    return values[len(values) // 2] if values else 0.0


def split_into_panels(
    metrics: list[str], unit_groups: dict[str, str], evidence: list[EvidenceArtifact],
) -> list[list[str]]:
    """Which metrics may share one set of axes.

    Metrics share a panel only when they have the same kind of unit (money, counts, ratios) AND a comparable
    size. One axis for a ₹290k total and a ₹1.9k average flattens the small one; one bar scale for a count and for
    rupees reads the count against the rupee gridlines. Each panel gets its own axis instead of a second one."""
    panels: list[list[str]] = []
    for group in dict.fromkeys(unit_groups.get(m, "") for m in metrics):
        members = sorted((m for m in metrics if unit_groups.get(m, "") == group),
                         key=lambda m: _metric_scale(evidence, m))
        current: list[str] = []
        base = 0.0
        for m in members:
            scale = _metric_scale(evidence, m)
            if current and base > 0 and scale > base * _PANEL_SCALE_RATIO:
                panels.append(current)
                current, base = [], 0.0
            if not current:
                base = scale
            current.append(m)
        if current:
            panels.append(current)
    order = {m: i for i, m in enumerate(metrics)}
    return sorted((sorted(p, key=order.__getitem__) for p in panels), key=lambda p: order[p[0]])


def _apply_panels(spec: dict[str, Any], evidence: list[EvidenceArtifact], metrics: list[str],
                  unit_groups: dict[str, str], units: dict[str, str]) -> None:
    """Give unlike series their own panel (own axis, shared x) instead of grouped bars on two axes."""
    if spec["chart_type"] not in ("line", "area", "bar", "grouped_bar") or len(metrics) < 2:
        return
    panels = split_into_panels(metrics, unit_groups, evidence)
    if len(panels) < 2:
        return
    if len(panels) > _MAX_PANELS:
        dropped = [m for p in panels[_MAX_PANELS:] for m in p]
        panels = panels[:_MAX_PANELS]
        spec.setdefault("warnings", []).append(
            f"chart shows {_MAX_PANELS} panels; left out {', '.join(_format_metric_label(m) for m in dropped)}")
        kept = {m for p in panels for m in p}
        spec["series"] = [s for s in spec["series"] if s.get("metric") in kept]
    mark = "bar" if spec["chart_type"] in ("bar", "grouped_bar") else "line"
    out_panels: list[dict[str, Any]] = []
    y_axes: list[dict[str, Any]] = []
    for index, members in enumerate(panels):
        keys = [s["key"] for s in spec["series"] if s.get("metric") in members]
        for s in spec["series"]:
            if s.get("metric") in members:
                s["yAxisIndex"] = index
                s["type"] = mark
        unit = units.get(members[0], "val")
        out_panels.append({
            "title": ", ".join(_format_metric_label(m) for m in members),
            "unit": unit, "series": keys, "mark": mark,
        })
        y_axes.append({"type": "value", "name": unit, "position": "left"})
    spec["panels"] = out_panels
    spec["yAxis"] = y_axes
    spec["xAxis"] = {**spec["xAxis"]}


def _build_waterfall(
    spec: dict[str, Any],
    evidence: list[EvidenceArtifact],
    metrics: list[str],
    varying_dim_keys: list[str],
) -> None:
    """Signed steps from zero to a closing total: each value is a step (positive up, negative down)."""
    if varying_dim_keys:
        steps = list(_group_by_dimension(evidence, varying_dim_keys).items())
    else:
        steps = [(_format_metric_label(m), next((float(e.value) for e in evidence
                                                 if e.metric_id == m and e.value is not None), 0.0)) for m in metrics]
    data: list[dict[str, Any]] = [{"name": n, "value": v, "kind": "delta"} for n, v in steps]
    data.append({"name": "Total", "value": sum(v for _, v in steps), "kind": "total"})
    spec["xAxis"] = {"type": "category", "key": "name"}
    spec["data"] = data
    spec["series"].append({"key": "value", "name": _format_metric_label(metrics[0]) if metrics else "Value",
                           "type": "waterfall", "metric": metrics[0] if metrics else ""})


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
    elif chart_type == "treemap" and len(data) > 24:
        head = data[:23]
        head.append({"name": "Other", "value": sum(v for _, v in rows[23:])})
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


def _pick_primary_metric(metrics: list[str], unit_groups: dict[str, str]) -> str:
    """One metric for a crowded multi-metric chart: the first ratio (the efficiency measure), else the first."""
    return next((m for m in metrics if unit_groups.get(m) == "ratio"), metrics[0])


def _category_key(item: EvidenceArtifact, dim_keys: list[str]) -> str:
    return _dimension_key(item, dim_keys) if dim_keys else "Total"


def thin_evidence_for_chart(
    evidence: list[EvidenceArtifact], *, cap_categories: bool = True,
) -> tuple[list[EvidenceArtifact], list[str]]:
    """Keep a chart readable: one metric when entities×metrics explode, top categories only.

    Returns the thinned evidence and human warnings describing what was dropped.
    Does not inspect user wording — only the evidence shape.
    """
    if not evidence:
        return evidence, []
    warnings: list[str] = []
    metrics = list(dict.fromkeys(e.metric_id for e in evidence))
    dim_keys = _varying_dimension_keys(evidence)
    categories = sorted({_category_key(e, dim_keys) for e in evidence}) if dim_keys else []
    projected = max(len(metrics), 1) * max(len(categories), 1)
    kept = list(evidence)

    if len(metrics) > 1 and len(categories) > 1 and projected > _MAX_CHART_SERIES:
        groups = unit_groups_for_evidence(evidence)
        primary = _pick_primary_metric(metrics, groups)
        kept = [e for e in kept if e.metric_id == primary]
        dropped = [m for m in metrics if m != primary]
        warnings.append(
            f"chart kept only {_format_metric_label(primary)} "
            f"(dropped {', '.join(_format_metric_label(m) for m in dropped)}); "
            "pass evidence for one metric per chart when comparing many entities"
        )
        metrics = [primary]

    ranking = len(metrics) == 1 and len({e.period_start for e in kept}) == 1
    cap = _MAX_RANKING_CATEGORIES if ranking else _MAX_CHART_CATEGORIES
    if cap_categories and dim_keys and len(categories) > cap:
        totals: dict[str, float] = defaultdict(float)
        for e in kept:
            if e.value is None:
                continue
            totals[_category_key(e, dim_keys)] += abs(float(e.value))
        ranked = sorted(totals, key=lambda k: totals[k], reverse=True)
        keep_cats = set(ranked[:cap])
        before = len(kept)
        kept = [e for e in kept if _category_key(e, dim_keys) in keep_cats]
        if len(kept) < before:
            warnings.append(
                f"chart shows top {cap} entities by magnitude "
                f"({len(categories) - cap} others omitted for readability)"
            )
    return kept, warnings


def generate_visualization_spec(
    evidence: list[EvidenceArtifact],
    intent: str = "",
    title: str = "Visualization",
    *,
    chart_type: ChartType | str | None = None,
    aggregations: dict[str, str] | None = None,
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a generic visualization specification from validated evidence.

    ``chart_type`` is the only thing that selects the form; ``intent`` is
    accepted for call-site compatibility and is never inspected to pick a form.
    An unrecognized ``chart_type`` is an error, not a silent substitution.
    ``aggregations`` (metric id -> catalogue aggregation) and ``labels`` (metric id -> catalogue name) refine the
    unit grouping and the series names.
    """
    token = _LABELS.set(dict(labels or {}))
    try:
        return _generate_spec(evidence, title, chart_type, aggregations)
    finally:
        _LABELS.reset(token)


def _generate_spec(
    evidence: list[EvidenceArtifact],
    title: str,
    chart_type: ChartType | str | None,
    aggregations: dict[str, str] | None,
) -> dict[str, Any]:
    if not evidence:
        return {"error": "No evidence provided."}

    requested = normalize_chart_type(chart_type) if chart_type else None
    if chart_type and requested is None:
        return {
            "error": (
                f"Unsupported chart_type {chart_type!r}. Supported forms: "
                f"{', '.join(CHART_TYPES)}."
            )
        }

    # A share of a whole keeps every slice (the pie folds the tail into "Other", the treemap holds two dozen).
    evidence, thin_warnings = thin_evidence_for_chart(evidence, cap_categories=requested not in COMPOSITION_FORMS)

    # Only an explicitly requested form can draw from a single point; a
    # trend form over one period says nothing.
    if len(evidence) < 2 and requested not in _SINGLE_POINT_FORMS:
        if thin_warnings:
            return {
                "error": (
                    "Chart evidence was thinned for readability and left fewer than "
                    "2 points. Pass evidence for one metric and the entities to plot."
                )
            }
        return {"error": "Insufficient data points for visualization. Need at least 2 data points."}

    shape = describe_evidence_shape(evidence)
    unit_groups = unit_groups_for_evidence(evidence, aggregations)
    resolved, warnings = reconcile_chart_type(requested, shape, unit_groups)
    warnings = [*thin_warnings, *warnings]

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
        _build_cartesian(spec, evidence, metrics, varying_dim_keys, time_labels, unit_groups)
        _apply_panels(spec, evidence, metrics, unit_groups, units)
    elif resolved in ("pie", "donut", "treemap", "funnel"):
        _build_composition(spec, evidence, metrics, varying_dim_keys)
    elif resolved == "waterfall":
        _build_waterfall(spec, evidence, metrics, varying_dim_keys)
    elif resolved == "radar":
        _build_radar(spec, evidence, varying_dim_keys)
    elif resolved == "heatmap":
        _build_heatmap(spec, evidence, metrics, varying_dim_keys)
    else:  # pragma: no cover - every declared form has a builder above
        return {"error": f"Chart form {resolved!r} has no builder."}

    return spec
