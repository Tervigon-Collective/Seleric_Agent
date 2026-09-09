"""Live-catalogue grounding for LLM classifications — shared by lookup_v1 and
swarm_v2 so metric/entity hints are never a local phrase table, in either
pipeline.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from seleric_swarm.runtime import SwarmRuntime
    from seleric_swarm.services.catalogue_bootstrap import CatalogueBootstrap
    from seleric_swarm.services.metrics import MetricRegistry

_STOPWORDS = {
    "what", "is", "the", "a", "an", "for", "on", "of", "and", "were", "was",
    "how", "many", "today", "yesterday", "last", "days", "in", "to",
    "get", "me", "report", "wise", "breakdown",
}

_MIN_DIM_TOKEN = 3
_TIME_SUFFIXES = ("_date", "_time", "_at")
# Generic English words that show up as a dimension-id suffix (fulfillment_status,
# order_status, ...) but are also common in ordinary phrasing ("funnel status",
# "ad performance status"). Matching on these alone false-positives a grain the
# user never asked for — require the fuller phrase (see dim_as_words check
# below) instead of a lone-token hit.
_GENERIC_DIM_TOKENS = {"status"}

# The catalogue's search endpoint doesn't return a relevance score, so token
# overlap with the query is the only ranking signal available. A single-token
# overlap only counts as a match when that token *is* the metric id/name
# (not just any shared word) to avoid false positives from generic terms.
_MIN_MULTI_TOKEN_OVERLAP = 2

_RESOLVE_DIM_CAP = "seleric.catalogue_resolve_dimension"
_RESOLVE_TERM_CAP = "seleric.catalogue_resolve_term"

# Grain/breakdown resolution (live catalogue_resolve_dimension included) is
# expensive and, worse, the live resolver has no relevance threshold — a
# single generic word like "status" comes back matching nearly every
# *_status dimension in the catalogue. Only worth asking at all when the
# query actually shows breakdown/grouping intent; a plain "X status" or
# "X performance" aggregate ask never should. Mirrors observer.py's
# _RANKING_LANGUAGE_RE gate for the same reason.
_GRAIN_LANGUAGE_RE = re.compile(
    r"\b(top|best|worst|highest|lowest|leading|lagging|breakdown|by|wise|split|per|grouped)\b",
    re.IGNORECASE,
)


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
        if overlap >= _MIN_MULTI_TOKEN_OVERLAP:
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


def _query_tokens(query: str) -> set[str]:
    return {
        tok
        for tok in re.findall(r"[a-z0-9]+", (query or "").lower().replace("-", " "))
        if tok not in _STOPWORDS and len(tok) >= _MIN_DIM_TOKEN
    }


def _is_time_dimension(dim_id: str) -> bool:
    low = (dim_id or "").lower()
    return any(low.endswith(suffix) for suffix in _TIME_SUFFIXES)


def dimensions_in_query(
    query: str,
    dimension_ids: set[str],
    alias_index: dict[str, str] | None = None,
) -> list[str]:
    """Catalogue dimension ids whose id-tokens or aliases appear in the query."""
    q_tokens = _query_tokens(query)
    if not q_tokens and not (query or "").strip():
        return []
    q_norm = (query or "").lower().replace("-", " ").replace("_", " ")
    hits: list[str] = []
    for dim in dimension_ids:
        if not dim or _is_time_dimension(dim):
            continue
        dim_tokens = [t for t in dim.lower().split("_") if len(t) >= _MIN_DIM_TOKEN]
        dim_as_words = dim.lower().replace("_", " ")
        if dim_as_words in q_norm or dim.lower() in q_tokens:
            hits.append(dim)
            continue
        if dim_tokens and any(t in q_tokens and t not in _GENERIC_DIM_TOKENS for t in dim_tokens):
            hits.append(dim)
    if alias_index:
        for alias, dim_id in alias_index.items():
            if not dim_id or _is_time_dimension(dim_id) or dim_id in hits:
                continue
            if len(alias) < _MIN_DIM_TOKEN:
                continue
            if alias in q_norm or alias.replace(" ", "") in q_tokens or alias in q_tokens:
                hits.append(dim_id)
    return list(dict.fromkeys(hits))


def preferred_grain(dimension_ids: list[str]) -> str | None:
    """Longest matched id wins (lt_channel over channel)."""
    if not dimension_ids:
        return None
    return max(dimension_ids, key=len)


def _supported_for_hint(
    hint: str,
    metrics: MetricRegistry,
    bootstrap: CatalogueBootstrap,
) -> list[str]:
    definition = metrics.get(hint)
    cat_id = getattr(definition, "catalogue_metric", None) if definition else None
    if not cat_id:
        return []
    meta = bootstrap.get(cat_id)
    return list(meta.supported_dimensions) if meta else []


def _default_spec(defaults: dict[str, Any], key: str) -> tuple[str | None, str | None]:
    spec = defaults.get(key)
    if spec is None:
        return None, None
    if isinstance(spec, str):
        return spec, None
    if isinstance(spec, dict):
        dim = spec.get("dimension") or spec.get("dimension_id")
        metric = spec.get("metric") or spec.get("metric_id") or spec.get("default_measure")
        return (str(dim) if dim else None), (str(metric) if metric else None)
    return None, None


def _defaults_for_query(defaults: dict[str, Any], query: str) -> tuple[str | None, str | None]:
    q_tokens = _query_tokens(query)
    q_low = (query or "").lower().replace("_", " ")
    for key in defaults:
        phrase = key.replace("_", " ")
        key_tokens = [t for t in key.replace("-", "_").split("_") if len(t) >= _MIN_DIM_TOKEN]
        if phrase in q_low or (key_tokens and all(t in q_tokens for t in key_tokens)):
            return _default_spec(defaults, key)
    return None, None


def pick_grain(
    candidates: list[str],
    *,
    query: str,
    defaults: dict[str, Any],
    registry_supported: set[str],
    catalogue_in_registry: Any = None,
) -> str | None:
    """Ambiguous channel-like grains: use catalogue grain_defaults when the
    named metric is in the local registry; otherwise a candidate a registry
    metric can actually slice.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    default_dim, default_metric = _defaults_for_query(defaults, query)
    registered = catalogue_in_registry or (lambda _cid: False)
    if (
        default_dim
        and default_dim in candidates
        and default_metric
        and registered(default_metric)
    ):
        return default_dim
    supported = [d for d in candidates if d in registry_supported]
    if supported:
        return max(supported, key=len)
    if default_dim and default_dim in candidates:
        return default_dim
    return max(candidates, key=len)


def _is_dimension_payload(result: dict[str, Any]) -> bool:
    if result.get("dimension_id") or result.get("dimension"):
        return True
    for cand in result.get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        if cand.get("dimension_id") or cand.get("dimension"):
            return True
        if cand.get("id") and not cand.get("metric_id"):
            return True
    return False


def _dims_from_resolve(result: dict[str, Any]) -> list[str]:
    if not _is_dimension_payload(result):
        return []
    kind = str(result.get("kind") or "")
    resolved = result.get("dimension_id") or result.get("dimension")
    if kind == "resolved" and resolved:
        return [str(resolved)]
    if resolved and kind != "ambiguous":
        return [str(resolved)]
    out: list[str] = []
    for cand in result.get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        if cand.get("metric_id") and not cand.get("dimension_id"):
            continue
        dim = cand.get("dimension_id") or cand.get("dimension") or cand.get("id")
        if dim:
            out.append(str(dim))
    return list(dict.fromkeys(out))


async def resolve_catalogue_dimension(query: str, *, runtime: SwarmRuntime) -> list[str]:
    """Dimension ids from catalogue_resolve_dimension (or typed resolve_term).

    Untyped metric-only resolve_term payloads are ignored.
    """
    mcp = getattr(runtime, "mcp", None)
    if mcp is None:
        return []
    attempts: list[tuple[str, dict[str, Any]]] = []
    caps = getattr(mcp, "capabilities", set()) or set()
    if _RESOLVE_DIM_CAP in caps:
        attempts.append((_RESOLVE_DIM_CAP, {"text": query}))
    if _RESOLVE_TERM_CAP in caps:
        attempts.append((_RESOLVE_TERM_CAP, {"text": query, "kind": "dimension"}))
    for capability, arguments in attempts:
        try:
            result = await mcp.call(
                agent_id="coordinator_agent",
                capability=capability,
                arguments=arguments,
            )
        except Exception:
            continue
        if isinstance(result, dict):
            dims = _dims_from_resolve(result)
            if dims:
                return dims
    return []


def constrain_hints_to_grain(
    hints: list[str],
    *,
    query: str,
    metrics: MetricRegistry,
    bootstrap: CatalogueBootstrap | None,
    resolved_grain: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Filter or replace metric hints so they can actually slice by a named grain.

    Returns (hints, resolved_dimensions). No-op when bootstrap is cold.
    """
    if bootstrap is None or not bootstrap.is_warm():
        return list(hints), []
    grain = [d for d in (resolved_grain or []) if d]
    if not grain:
        grain = dimensions_in_query(query, bootstrap.dimension_ids(), bootstrap.alias_index())
    if not grain:
        return list(hints), []

    registry_supported: set[str] = set()
    for definition in metrics.all():
        cat_id = getattr(definition, "catalogue_metric", None)
        if not cat_id:
            continue
        meta = bootstrap.get(cat_id)
        if meta:
            registry_supported.update(meta.supported_dimensions or [])

    preferred = pick_grain(
        grain,
        query=query,
        defaults=bootstrap.grain_defaults(),
        registry_supported=registry_supported,
        catalogue_in_registry=lambda cid: metrics.id_for_catalogue(cid) is not None,
    ) or preferred_grain(grain)
    resolved = [preferred] if preferred else grain[:1]

    supporting = [
        h for h in hints if set(_supported_for_hint(h, metrics, bootstrap)) & set(grain)
    ]
    if supporting:
        return supporting, resolved

    _, default_metric = _defaults_for_query(bootstrap.grain_defaults(), query)
    if default_metric:
        hid = metrics.id_for_catalogue(default_metric)
        if hid:
            return [hid], resolved

    filled: list[str] = []
    target = set(resolved)
    for definition in metrics.all():
        cat_id = getattr(definition, "catalogue_metric", None) or definition.id
        if not cat_id:
            continue
        meta = bootstrap.get(cat_id) or bootstrap.get(definition.id)
        if meta is None:
            continue
        if target & set(meta.supported_dimensions or []):
            mid = definition.id
            if mid not in filled:
                filled.append(mid)
    return filled, resolved


def evidence_covers_grain(evidence: list[dict], resolved_dimensions: list[str]) -> bool:
    """True when no grain was asked, or at least one evidence row carries it."""
    wanted = [d for d in resolved_dimensions if d]
    if not wanted:
        return True
    return any(
        (row.get("dimensions") or {}).get(dim) not in (None, "")
        for row in evidence or []
        for dim in wanted
    )


async def apply_catalogue_grain(
    query: str,
    hints: list[str],
    *,
    runtime: SwarmRuntime,
) -> tuple[list[str], list[str]]:
    bootstrap = getattr(runtime, "bootstrap", None)
    if bootstrap is None:
        return list(hints), []
    if not _GRAIN_LANGUAGE_RE.search(query or ""):
        return list(hints), []
    await bootstrap.refresh_if_stale()
    runtime.metrics.bind_catalogue(bootstrap)
    resolved = await resolve_catalogue_dimension(query, runtime=runtime)
    if resolved:
        # The live resolver has no relevance threshold and can return a
        # dimension with no real connection to the query (e.g. "top channels
        # by sessions" resolving to "item_count"). Only trust suggestions the
        # query text itself corroborates; anything else falls through to the
        # local, query-grounded heuristic below instead of silently steering
        # the mission at a domain the resolver hallucinated.
        resolved = dimensions_in_query(query, set(resolved))
    return constrain_hints_to_grain(
        hints,
        query=query,
        metrics=runtime.metrics,
        bootstrap=bootstrap,
        resolved_grain=resolved or None,
    )
