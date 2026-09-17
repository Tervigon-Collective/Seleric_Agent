"""Registry metric_id → live catalogue measure id (bootstrap cache, then exact, then search)."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from seleric_swarm.services.catalogue_bootstrap import CatalogueBootstrap
from seleric_swarm.services.metrics import MetricDefinition, MetricRegistry


def measure_keywords_overlap(definition: MetricDefinition, candidate_id: str) -> bool:
    skip = {"metric"}
    id_tokens = {
        t.lower()
        for t in re.split(r"[._]", definition.id)
        if len(t) > 2 and t.lower() not in skip
    }
    candidate_tokens = {t.lower() for t in candidate_id.split("_") if len(t) > 2}
    return bool(id_tokens & candidate_tokens)


def module_args(definition: MetricDefinition) -> dict[str, Any]:
    if "seleric_module" in definition.raw:
        return {"module": definition.seleric_module}
    return {}


async def resolve_measure(
    definition: MetricDefinition,
    *,
    mcp: Any,
    agent_id: str,
    bootstrap: CatalogueBootstrap | None = None,
    metrics: MetricRegistry | None = None,
    cache: dict[str, str | None] | None = None,
    on_stale_sub: Callable[[str, str, str], None] | None = None,
) -> str | None:
    """Return the live catalogue id, or None. Never returns a stale preferred id."""
    if cache is not None and definition.id in cache:
        return cache[definition.id]

    preferred = definition.catalogue_metric

    if bootstrap is not None:
        if bootstrap.should_refresh():
            hints = []
            if metrics is not None:
                hints = [m.catalogue_metric for m in metrics.yaml_all() if m.catalogue_metric]
            await bootstrap.warm(registry_hints=hints or None)
            if metrics is not None:
                metrics.bind_catalogue(bootstrap)
        if preferred and bootstrap.has(preferred):
            if cache is not None:
                cache[definition.id] = preferred
            return preferred

    extra = module_args(definition)

    caps = getattr(mcp, "capabilities", None)
    if preferred:
        if caps is not None and "seleric.catalogue_get_metric" not in caps:
            if cache is not None:
                cache[definition.id] = preferred
            return preferred
        try:
            exact_result = await mcp.call(
                agent_id=agent_id,
                capability="seleric.catalogue_get_metric",
                arguments={"metric_id": preferred, **extra},
            )
        except Exception:
            if cache is not None:
                cache[definition.id] = None
            return None
        if isinstance(exact_result, dict) and not exact_result.get("error"):
            if cache is not None:
                cache[definition.id] = preferred
            return preferred

    try:
        desc_result = await mcp.call(
            agent_id=agent_id,
            capability="seleric.catalogue_search_metrics",
            arguments={"query": definition.description, **extra},
        )
    except Exception:
        if cache is not None:
            cache[definition.id] = None
        return None

    for match in (desc_result.get("matches") or []) if isinstance(desc_result, dict) else []:
        candidate = match.get("id") or ""
        if candidate and measure_keywords_overlap(definition, candidate):
            if preferred and on_stale_sub is not None:
                on_stale_sub(definition.id, preferred, candidate)
            if cache is not None:
                cache[definition.id] = candidate
            return candidate

    if cache is not None:
        cache[definition.id] = None
    return None
