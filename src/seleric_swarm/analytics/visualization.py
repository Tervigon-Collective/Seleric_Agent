from __future__ import annotations

from collections import defaultdict
from typing import Any

from seleric_swarm.agent.artifacts import EvidenceArtifact


def _format_time_label(dt: Any) -> str:
    if hasattr(dt, "strftime"):
        return dt.strftime("%Y-%m-%d")
    return str(dt)[:10]


def _format_metric_label(metric_id: str) -> str:
    return metric_id.removeprefix("metric.").replace("_", " ").title()


def _group_by_dimension(evidence: list[EvidenceArtifact], dimension_keys: list[str]) -> dict[str, float]:
    """Helper to group data by dimension for bar/pie charts."""
    result: dict[str, float] = defaultdict(float)
    for item in evidence:
        if dimension_keys and item.dimensions:
            parts = [v for dim in dimension_keys if (v := item.dimensions.get(dim)) is not None]
            key = " - ".join(parts) if parts else "Total"
        elif item.dimensions:
            key = " - ".join(v for _, v in sorted(item.dimensions.items()) if v is not None)
        else:
            key = _format_metric_label(item.metric_id)
        if item.value is not None:
            result[key] += float(item.value)
    return dict(result)


def _group_by_time_multi(
    evidence: list[EvidenceArtifact],
    metrics: list[str],
    key_builder: Any,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Group time series data by time string and dynamic series keys."""
    time_series: dict[str, dict[str, float]] = defaultdict(dict)
    all_keys = set()
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


def generate_visualization_spec(
    evidence: list[EvidenceArtifact],
    intent: str,
    title: str = "Visualization",
) -> dict[str, Any]:
    """Takes validated EvidenceArtifacts and outputs a generic visualization specification.

    This schema is designed to be easily mapped to ECharts in the frontend.
    """
    if not evidence:
        return {"error": "No evidence provided."}

    intent_lower = intent.lower()

    # Rule: Fail if fewer than 2 points unless specific intent allows
    if len(evidence) < 2 and "gauge" not in intent_lower and "single" not in intent_lower:
        return {"error": "Insufficient data points for visualization. Need at least 2 data points."}

    # Extract all metrics and dimensions
    metrics = list(dict.fromkeys(e.metric_id for e in evidence))
    all_dims: set[str] = set()
    for e in evidence:
        all_dims.update(e.dimensions.keys())
    dimensions = list(all_dims)

    # Detect if we have multiple time periods
    time_periods = set(e.period_start for e in evidence)
    has_time = len(time_periods) > 1

    chart_type = "bar"  # Default fallback

    if "funnel" in intent_lower:
        chart_type = "funnel"
    elif any(k in intent_lower for k in ("composition", "share", "pie", "donut")):
        chart_type = "pie"
    elif any(k in intent_lower for k in ("trend", "time", "history")) or has_time:
        chart_type = "area" if "area" in intent_lower else "line"
    elif any(k in intent_lower for k in ("bar", "compare", "rank", "breakdown")):
        chart_type = "bar"

    # Multi-metric handling for different units
    units = {e.metric_id: e.unit or "val" for e in evidence}
    unique_units = list(dict.fromkeys(units.values()))

    spec: dict[str, Any] = {
        "title": title,
        "chart_type": chart_type,
        "metrics": metrics,
        "dimensions": dimensions,
        "series": [],
        "xAxis": {},
        "yAxis": [],
        "data": [],
    }

    # Populate Y-Axes based on unique units
    for idx, u in enumerate(unique_units):
        spec["yAxis"].append({
            "type": "value",
            "name": u,
            "position": "left" if idx == 0 else "right",
        })

    if chart_type in ["line", "area"]:
        has_dims = bool(dimensions)
        has_multi_metrics = len(metrics) > 1

        def build_key(item: EvidenceArtifact) -> str:
            label = _format_metric_label(item.metric_id)
            if has_dims and item.dimensions:
                dim_str = " - ".join(v for _, v in sorted(item.dimensions.items()) if v is not None)
                return f"{label} ({dim_str})" if has_multi_metrics else dim_str
            return label

        data_rows, series_names = _group_by_time_multi(evidence, metrics, build_key)
        spec["xAxis"] = {"type": "category", "key": "time"}
        spec["data"] = data_rows

        for s_name in series_names:
            # Map unit from first matching metric
            matched_metric = next((m for m in metrics if _format_metric_label(m) in s_name or m in s_name), metrics[0])
            u_idx = unique_units.index(units[matched_metric]) if matched_metric in units else 0
            series_entry: dict[str, Any] = {
                "key": s_name,
                "name": s_name,
                "type": "line",
                "yAxisIndex": u_idx,
            }
            if chart_type == "area":
                series_entry["areaStyle"] = {"opacity": 0.25}
            spec["series"].append(series_entry)

    elif chart_type == "funnel":
        data = []
        for e in evidence:
            label = _format_metric_label(e.metric_id)
            if e.dimensions:
                dim_str = " - ".join(v for _, v in sorted(e.dimensions.items()) if v is not None)
                label = f"{label} ({dim_str})"
            if e.value is not None:
                data.append({"name": label, "value": float(e.value)})

        spec["data"] = data
        spec["series"].append({
            "key": "value",
            "name": "Funnel",
            "type": "funnel",
        })

    elif chart_type in ["bar", "pie"]:
        grouped = _group_by_dimension(evidence, dimensions)
        data = [{"name": k, "value": v} for k, v in grouped.items()]

        spec["xAxis"] = {"type": "category", "key": "name"}
        spec["data"] = data

        main_metric = _format_metric_label(metrics[0]) if metrics else "Value"

        if chart_type == "pie":
            spec["data"] = sorted(spec["data"], key=lambda x: x["value"], reverse=True)
            if len(spec["data"]) > 7:
                top = spec["data"][:6]
                other_val = sum(float(x["value"]) for x in spec["data"][6:])
                top.append({"name": "Other", "value": other_val})
                spec["data"] = top
            spec["series"].append({"key": "value", "name": main_metric, "type": "pie"})
        else:
            # Bar chart
            spec["data"] = sorted(spec["data"], key=lambda x: x["value"], reverse=True)
            if len(spec["data"]) > 15:
                spec["data"] = spec["data"][:15]
                spec["title"] += " (Top 15)"
            spec["series"].append({
                "key": "value",
                "name": main_metric,
                "type": "bar",
                "yAxisIndex": 0,
            })

    return spec

