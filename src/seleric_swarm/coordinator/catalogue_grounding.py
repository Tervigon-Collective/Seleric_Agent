"""Live-catalogue grounding for LLM classifications — shared by lookup_v1 and
swarm_v2 so metric/entity hints are never a local phrase table, in either
pipeline.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from seleric_swarm.runtime import SwarmRuntime

_STOPWORDS = {
    "what", "is", "the", "a", "an", "for", "on", "of", "and", "were", "was",
    "how", "many", "today", "yesterday", "last", "days", "in", "to",
}


async def hints_from_catalogue(query: str, *, runtime: SwarmRuntime, agent_id: str = "coordinator_agent") -> list[str]:
    """Resolve query language to registry metric ids via catalogue_search_metrics."""
    if "seleric.catalogue_search_metrics" not in runtime.mcp.capabilities:
        return []
    try:
        result = await runtime.mcp.call(
            agent_id=agent_id,
            capability="seleric.catalogue_search_metrics",
            arguments={"query": query},
        )
    except Exception:
        return []
    out: list[str] = []
    scored: list[tuple[int, str]] = []
    q_tokens = {tok for tok in re.findall(r"[a-z0-9]+", (query or "").lower()) if tok not in _STOPWORDS}
    for match in result.get("matches") or []:
        how = str(match.get("matched_on") or "")
        if how.startswith("description"):
            continue
        registry_id = runtime.metrics.id_for_catalogue(match.get("id"))
        if not registry_id:
            continue
        hay = f"{match.get('id') or ''} {match.get('display_name') or ''}".lower().replace("_", " ")
        hay_tokens = set(re.findall(r"[a-z0-9]+", hay))
        overlap_tokens = hay_tokens & q_tokens
        overlap = len(overlap_tokens)
        cid = str(match.get("id") or "")
        if overlap >= 2:
            scored.append((overlap, registry_id))
        elif overlap == 1:
            tok = next(iter(overlap_tokens))
            if tok == cid or cid.split("_") == [tok]:
                scored.append((overlap, registry_id))
    if not scored:
        return []
    best = max(item[0] for item in scored)
    for overlap, registry_id in scored:
        if overlap == best and registry_id not in out:
            out.append(registry_id)
    return out


async def entities_from_catalogue(
    query: str, metric_id: str, *, runtime: SwarmRuntime, agent_id: str = "coordinator_agent"
) -> list[str]:
    """Resolve entity-like tokens in the query against a metric's supported dimensions."""
    definition = runtime.metrics.get(metric_id)
    if definition is None or "seleric.catalogue_get_metric" not in runtime.mcp.capabilities:
        return []
    try:
        payload = await runtime.mcp.call(
            agent_id=agent_id,
            capability="seleric.catalogue_get_metric",
            arguments={"metric_id": definition.catalogue_metric},
        )
    except Exception:
        return []
    supported = list(payload.get("supported_dimensions") or [])
    text = (query or "").lower()
    hits: list[str] = []
    for dim in supported:
        raw = str(dim)
        parts = [p for p in raw.removeprefix("lt_").split("_") if p]
        token = " ".join(parts)
        if raw.lower() in text or (token and token in text):
            hits.append(raw)
            continue
        if any(part in text for part in parts if len(part) >= 4):
            hits.append(raw)
    return hits
