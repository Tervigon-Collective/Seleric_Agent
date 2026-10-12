"""Build the metric graph from the read model plus a parsed causal-graph config.

Identity edges come from Core's canonical contract (SEM-14):
- Ratio metrics: use canonical ``ratio_components`` (numerator/denominator)
- Additive metrics: use canonical ``formula.depends_on`` with coefficients

Cross-domain extensions (AG-06):
- Verified joins vs attributed relationships
- Time basis tracking (event_time vs cohort_time vs order_time)
- Spend attribution (direct vs allocated vs shared)
- Population coverage (attributed vs unmatched vs partial)
- Contribution type (observed vs causal vs post_ad)
- Profit level (post_ad vs contribution_margin vs net_profit)
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from seleric_swarm.graph.model import Edge, MetricGraph, SkippedNode
from seleric_swarm.semantic import SemanticReadModel


def _edge_cross_domain_attrs(
    source_metric: Any,
    target_metric: Any,
    role: str,
) -> dict[str, Any]:
    """Derive cross-domain edge attributes from Core metric metadata (AG-06)."""
    attrs = {
        "join_type": "derived",
        "time_basis": "event_time",
        "spend_attribution": "direct",
        "population_coverage": "attributed",
        "contribution_type": "observed",
        "profit_level": "post_ad",
    }

    # Join type: verified for ratio components, attributed for formula depends_on/term, derived for legacy
    if role in ("numerator", "denominator"):
        attrs["join_type"] = "verified"
    elif role in ("numerator", "denominator", "term"):
        attrs["join_type"] = "attributed"
    elif role == "depends_on":
        attrs["join_type"] = "derived"

    # Time basis: from source metric's date_basis/date_twin/grain
    source_grain = getattr(source_metric, "grain", "") or ""
    source_date_basis = getattr(source_metric, "date_basis", "") or ""
    source_date_twin = getattr(source_metric, "date_twin", "") or ""
    if "order" in source_grain or "order" in source_date_basis or "order" in source_date_twin:
        attrs["time_basis"] = "order_time"
    elif "cohort" in source_grain or "cohort" in source_date_basis:
        attrs["time_basis"] = "cohort_time"
    elif "report" in source_grain or "report" in source_date_basis:
        attrs["time_basis"] = "report_time"

    # Spend attribution: check if TARGET metric has allocated spend (formula depends_on with ad_spend)
    # This handles cases where target metric's formula allocates spend from source
    target_formula = getattr(target_metric, "formula", None)
    if target_formula and isinstance(target_formula.get("depends_on"), list):
        for dep in target_formula["depends_on"]:
            dep_metric = dep.get("metric", "") if isinstance(dep, dict) else dep if isinstance(dep, str) else ""
            if "ad_spend" in dep_metric or "spend" in dep_metric:
                attrs["spend_attribution"] = "allocated"
                break
    # Source category direct spend (only if not already allocated)
    source_category = getattr(source_metric, "category", "") or ""
    if source_category == "advertising" and attrs["spend_attribution"] != "allocated":
        attrs["spend_attribution"] = "direct"

    # Population coverage: check for attributed vs unmatched vs partial
    target_category = getattr(target_metric, "category", "") or ""
    if target_category == "attribution":
        attrs["population_coverage"] = "attributed"
    elif "unmatched" in str(getattr(target_metric, "id", "")).lower():
        attrs["population_coverage"] = "unmatched"
    elif role in ("depends_on", "term"):
        attrs["population_coverage"] = "partial"

    # Contribution type: causal for driver edges, post_ad for post-ad contribution
    if role in ("numerator", "denominator"):
        attrs["contribution_type"] = "causal"
    elif target_category == "finance" and "profit" in str(getattr(target_metric, "id", "")).lower():
        attrs["contribution_type"] = "post_ad"

    # Profit level: check target metric's category and id
    if "net_profit" in str(getattr(target_metric, "id", "")).lower():
        attrs["profit_level"] = "net_profit"
    elif "contribution_margin" in str(getattr(target_metric, "id", "")).lower():
        attrs["profit_level"] = "contribution_margin"
    elif target_category == "finance" and "profit" in str(getattr(target_metric, "id", "")).lower():
        attrs["profit_level"] = "net_profit"

    return attrs


def identity_edges(read_model: SemanticReadModel) -> list[Edge]:
    """Component → composite edges from Core's canonical contract (SEM-14).

    - Ratio metrics: use canonical ``ratio_components`` (numerator/denominator)
    - Additive metrics: use canonical ``formula.depends_on`` with coefficients
    - Fallback to legacy ``composition`` / ``depends_on`` for backwards compatibility

    Cross-domain extensions (AG-06): adds join_type, time_basis, spend_attribution,
    population_coverage, contribution_type, profit_level to edges.
    """
    known = read_model.metric_ids()
    metric_by_id = {m.id: m for m in read_model.metrics}
    out: list[Edge] = []
    for m in read_model.metrics:
        # Ratio metrics: use canonical ratio_components (SEM-14)
        if m.ratio_components and isinstance(m.ratio_components, dict):
            if isinstance(m.ratio_components.get("numerator"), str):
                src_id = m.ratio_components["numerator"]
                src_metric = metric_by_id.get(src_id)
                attrs = _edge_cross_domain_attrs(metric_by_id.get(src_id), m, "numerator")
                out.append(Edge(source=src_id, target=m.id, kind="identity", role="numerator",
                                coefficient=None, **attrs))
            if isinstance(m.ratio_components.get("denominator"), str):
                src_id = m.ratio_components["denominator"]
                attrs = _edge_cross_domain_attrs(metric_by_id.get(src_id), m, "denominator")
                out.append(Edge(source=src_id, target=m.id, kind="identity", role="denominator",
                                coefficient=None, **attrs))
        # Additive/composite: use canonical formula.depends_on with coefficients
        elif m.formula and isinstance(m.formula.get("depends_on"), list):
            for dep in m.formula["depends_on"]:
                if isinstance(dep, str):
                    attrs = _edge_cross_domain_attrs(metric_by_id.get(dep), m, "term")
                    out.append(Edge(source=dep, target=m.id, kind="identity", role="term",
                                    coefficient=None, **attrs))
                elif isinstance(dep, dict) and isinstance(dep.get("metric"), str):
                    coef = dep.get("coefficient", 1.0)
                    attrs = _edge_cross_domain_attrs(metric_by_id.get(dep["metric"]), m, "term")
                    out.append(Edge(source=dep["metric"], target=m.id, kind="identity", role="term",
                                    coefficient=float(coef) if isinstance(coef, (int, float)) else None, **attrs))
        # Legacy fallback (deprecated)
        elif m.composition:
            for source, role, coefficient in _components(m.composition, m.extra.get("depends_on")):
                if source in known and source != m.id:
                    attrs = _edge_cross_domain_attrs(metric_by_id.get(source), m, role)
                    out.append(Edge(source=source, target=m.id, kind="identity", role=role,
                                    coefficient=coefficient, **attrs))
    # Deduplicate by (source, target, role)
    seen = set()
    deduped = []
    for e in out:
        key = (e.source, e.target, e.role)
        if key not in seen:
            seen.add(key)
            deduped.append(e)
    return deduped


def _components(composition: Any, depends_on: Any) -> list[tuple[str, str, float | None]]:
    """Legacy fallback for pre-Core catalogues. Deprecated."""
    out: list[tuple[str, str, float | None]] = []
    terms: Any = composition
    if isinstance(composition, Mapping):
        for role in ("numerator", "denominator"):
            if isinstance(composition.get(role), str):
                out.append((composition[role], role, None))
        terms = composition.get("terms") or composition.get("components")
        depends_on = depends_on or composition.get("depends_on")
    if isinstance(terms, (list, tuple)):
        for term in terms:
            if isinstance(term, Mapping) and isinstance(term.get("metric"), str):
                coef = term.get("coefficient", 1.0)
                out.append((term["metric"], "term", float(coef) if isinstance(coef, (int, float)) else None))
            elif isinstance(term, str):
                out.append((term, "term", 1.0))
    if isinstance(depends_on, (list, tuple)):
        out += [(d, "depends_on", None) for d in depends_on if isinstance(d, str)]
    return out


def causal_edges(
    config: Mapping[str, Any] | None, read_model: SemanticReadModel
) -> tuple[list[Edge], list[SkippedNode]]:
    """Edges from ``{"graphs": [{"id", "edges": [[cause, effect] | {source, target, sign, lag_days}]}]}``.

    A node must be a catalogue metric or dimension id; anything else is skipped and reported
    (semantic pickup: renaming a metric in the catalogue drops its edges instead of breaking).

    Cross-domain extensions (AG-06): adds time_basis, contribution_type, profit_level to causal edges.
    """
    known = read_model.metric_ids() | {d.id for d in read_model.dimensions}
    metric_by_id = {m.id: m for m in read_model.metrics}
    edges: list[Edge] = []
    skipped: dict[tuple[str, str], SkippedNode] = {}
    for graph in (config or {}).get("graphs") or []:
        if not isinstance(graph, Mapping):
            continue
        gid = str(graph.get("id") or "")
        for raw in graph.get("edges") or []:
            parsed = _edge(raw)
            if parsed is None:
                continue
            source, target, sign, lag = parsed
            missing = [n for n in (source, target) if n not in known]
            for n in missing:
                skipped[(gid, n)] = SkippedNode(node=n, graph_id=gid, reason="not_in_catalogue")
            if not missing:
                src_metric = metric_by_id.get(source)
                tgt_metric = metric_by_id.get(target)
                # Cross-domain attributes for causal edges
                attrs = {
                    "time_basis": "event_time",
                    "contribution_type": "causal",
                    "profit_level": "post_ad",
                }
                # Time basis from source metric
                if src_metric:
                    src_grain = getattr(src_metric, "grain", "") or ""
                    src_date_basis = getattr(src_metric, "date_basis", "") or ""
                    if "order" in src_grain or "order" in (getattr(src_metric, "date_basis", "") or ""):
                        attrs["time_basis"] = "order_time"
                    elif "cohort" in src_grain:
                        attrs["time_basis"] = "cohort_time"
                # Profit level for causal edges
                tgt_id = getattr(tgt_metric, "id", "")
                if "net_profit" in tgt_id.lower():
                    attrs["profit_level"] = "net_profit"
                elif "contribution_margin" in tgt_id.lower():
                    attrs["profit_level"] = "contribution_margin"
                edges.append(Edge(source=source, target=target, kind="causal", sign=sign,
                                lag_days=lag, graph_id=gid, **attrs))
    return edges, list(skipped.values())


def _edge(raw: Any) -> tuple[str, str, Literal[1, -1, 0], int] | None:
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        source, target = raw[0], raw[1]
        sign = raw[2] if len(raw) > 2 else 0
        lag = raw[3] if len(raw) > 3 else 0
    elif isinstance(raw, Mapping):
        source, target = raw.get("source"), raw.get("target")
        sign, lag = raw.get("sign", 0), raw.get("lag_days", 0)
    else:
        return None
    if not isinstance(source, str) or not isinstance(target, str):
        return None
    sign_i: Literal[1, -1, 0] = 1 if sign == 1 else -1 if sign == -1 else 0
    return source, target, sign_i, int(lag) if isinstance(lag, int) and lag >= 0 else 0


def build_graph(read_model: SemanticReadModel, causal_config: Mapping[str, Any] | None = None) -> MetricGraph:
    causal, skipped = causal_edges(causal_config, read_model)
    return MetricGraph(
        catalogue_version=read_model.version,
        edges=tuple(identity_edges(read_model) + causal),
        skipped=tuple(skipped),
    )
