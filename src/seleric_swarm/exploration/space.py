"""The explorable data space, read from the live catalogue.

Exploration walks a graph the catalogue already describes: metrics (nodes,
with lineage edges from ``formula.depends_on``), the dimensions each metric can
be broken down by (hierarchy roots, enumerated low-cardinality axes) and the
views they live in. These planners pick *which* of those to look at; nothing
here names a metric, dimension or value.

``scope_dimensions`` and ``plan_dimensions`` started life inside
``toolsets/diagnosis.py``; they live here so the diagnosis and exploration
tools plan from one definition of "a dimension worth screening".
"""

from __future__ import annotations

from seleric_swarm.causal import diagnosis as engine
from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot



def scope_dimensions(catalogue: CatalogueSnapshot) -> set[str]:
    """The tenant key every query is scoped to (the runtime injects it), never a split.

    "Carried by nearly every view" stopped meaning tenant key once the business
    axes (platform, campaign, channel) were conformed onto every view: campaign
    was then dropped from every localisation and its words from every grain
    (live 2026-10-08, an ads diagnosis that never looked at campaigns).
    """
    from seleric_swarm.services.mcp_query import _BRAND_DIM_KEYS

    dims = set(catalogue.dimensions) | {d for m in catalogue.metrics for d in (m.supported_dimensions or [])}
    return {d for d in dims if d.lower() in _BRAND_DIM_KEYS}


def plan_dimensions(
    catalogue: CatalogueSnapshot, metric_id: str, cap: int, exclude: set[str] | frozenset[str] = frozenset()
) -> list[str]:
    """The metric's breakdown dimensions worth screening, coarsest business axes first."""
    scope = scope_dimensions(catalogue)
    views_per_dim: dict[str, int] = {}
    for m in catalogue.metrics:
        for d in m.supported_dimensions or []:
            views_per_dim[d] = views_per_dim.get(d, 0) + 1
    ranked: list[tuple[tuple[int, int, int, str], str]] = []
    for d in catalogue.supported_dimensions_for(metric_id):
        if d in scope or d in exclude or catalogue.is_time_dimension(d):
            continue
        level = catalogue.dimension_fact(d, "hierarchy_level")
        enumerated = catalogue.dimension_fact(d, "n_allowed") > 1
        # The business hierarchy first, coarsest level first (its deeper levels
        # are where a launch, a pause or a budget move shows up), then
        # enumerated (known low-cardinality) dims, then the rest by how widely
        # they are modelled.
        tier = 0 if level >= 1 else 1 if enumerated else 2
        ranked.append(((tier, level if level >= 1 else 0, -views_per_dim.get(d, 0), d), d))
    ranked.sort()
    return [d for _, d in ranked][:cap]


def headline_metrics(
    catalogue: CatalogueSnapshot, lineage: dict[str, engine.MetricMeta], cap: int
) -> list[str]:
    """The metrics an open-ended exploration starts from.

    Additive metrics that other metrics are built on (lineage hub score) are the
    business's base quantities — revenue, orders, spend, sessions — whatever the
    catalogue calls them. Ranked by hub score, then by how many breakdowns they
    support, and taken round-robin across views so one wide view cannot fill
    every slot. A metric's date twin (the same measure on another date axis) is
    skipped once its twin is picked.
    """
    hub = engine.lineage_in_degree(lineage)
    scope = scope_dimensions(catalogue)
    scored: list[tuple[tuple[int, int, str], str, str]] = []
    for m in catalogue.metrics:
        meta = lineage.get(m.id)
        if meta is None or not meta.additive:
            continue
        dims = [d for d in m.supported_dimensions or [] if d not in scope and not catalogue.is_time_dimension(d)]
        scored.append(((-hub.get(m.id, 0), -len(dims), m.id), m.id, m.view))
    scored.sort()
    by_view: dict[str, list[str]] = {}
    order: list[str] = []
    for _, mid, view in scored:
        if view not in by_view:
            order.append(view)
        by_view.setdefault(view, []).append(mid)
    picked: list[str] = []
    twins: set[str] = set()
    while len(picked) < cap and any(by_view.values()):
        for view in order:
            while by_view[view] and len(picked) < cap:
                mid = by_view[view].pop(0)
                if mid in twins:
                    continue
                picked.append(mid)
                if twin := catalogue.date_basis_for(mid)[1]:
                    twins.add(twin)
                break
    return picked
