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

from functools import lru_cache
from typing import Any

import yaml

from seleric_swarm.causal import diagnosis as engine
from seleric_swarm.paths import repo_root
from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot


@lru_cache(maxsize=1)
def _scope_file() -> dict[str, Any]:
    path = repo_root() / "config" / "scope.yaml"
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def configured_scope_ids() -> tuple[str, ...]:
    """Tenant dimension ids. Settings override the file; the file is the default."""
    from seleric_swarm.config.settings import get_settings

    override = get_settings().scope_dimension_ids.strip()
    if override:
        return tuple(part.strip() for part in override.split(",") if part.strip())
    raw = _scope_file().get("scope_dimension_ids") or []
    if isinstance(raw, str):
        return tuple(part.strip() for part in raw.split(",") if part.strip())
    return tuple(str(item) for item in raw if item)


def default_brand_id() -> str:
    """Workspace brand. ``settings.default_brand_id`` overrides ``config/scope.yaml``."""
    from seleric_swarm.config.settings import get_settings

    override = get_settings().default_brand_id.strip()
    if override:
        return override
    return str(_scope_file().get("default_brand_id") or "")


def _catalogue_dimension_ids(catalogue: Any) -> set[str]:
    ids: set[str] = set()
    for dim in getattr(catalogue, "dimensions", ()) or ():
        if isinstance(dim, str):
            ids.add(dim)
        else:
            dim_id = getattr(dim, "id", "")
            if dim_id:
                ids.add(str(dim_id))
    for metric in getattr(catalogue, "metrics", ()) or ():
        ids.update(str(dim) for dim in (getattr(metric, "supported_dimensions", ()) or []) if dim)
    return ids


def scope_dimensions(catalogue: CatalogueSnapshot | Any) -> set[str]:
    """The tenant key every query is scoped to, never a split.

    A snapshot that declares ``scope_dimension_ids`` wins. Otherwise the
    configured ids are intersected with the dimensions the catalogue actually carries.
    """
    dims = _catalogue_dimension_ids(catalogue)
    declared = tuple(getattr(catalogue, "scope_dimension_ids", ()) or ())
    keys = {item.lower() for item in declared} if declared else {item.lower() for item in configured_scope_ids()}
    return {dim for dim in dims if dim.lower() in keys}


def scope_keys(catalogue: Any | None = None) -> frozenset[str]:
    """Lower-cased tenant dimension ids. An empty catalogue still uses the configured ids."""
    if catalogue is not None:
        found = {dim.lower() for dim in scope_dimensions(catalogue)}
        metrics = getattr(catalogue, "metrics", ()) or ()
        if found or metrics:
            return frozenset(found)
    return frozenset(item.lower() for item in configured_scope_ids())


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
