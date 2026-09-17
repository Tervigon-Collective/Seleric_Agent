"""Live-catalogue grounding for LLM classifications — shared by lookup_v1 and
swarm_v2 so metric/entity hints are never a local phrase table, in either
pipeline.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from seleric_swarm.services.metrics import cadence_stem, is_intraday_id

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
_GENERIC_DIM_TOKENS = {
    "status",
    # Calendar words are single-token overlaps with unrelated dimensions
    # (session_day_of_week, report_date, ...) whenever the query is asking
    # for time granularity ("per day", "daily", "by month"), not a real
    # dimensional breakdown. Same hallucination class as "status" ->
    # "fulfillment_status" -- see ground_live_grain's docstring.
    "day",
    "days",
    "daily",
    "week",
    "weeks",
    "weekly",
    "month",
    "months",
    "monthly",
    "year",
    "years",
    "yearly",
    "date",
}
_MIN_MULTI_TOKEN_OVERLAP = 2
_RESOLVE_DIM_CAP = "seleric.catalogue_resolve_dimension"
_RESOLVE_TERM_CAP = "seleric.catalogue_resolve_term"
_RESOLVED_TERM_KINDS = frozenset({"resolved", "auto_resolved"})
_GRAIN_WORDS = frozenset({
    "top", "best", "worst", "highest", "lowest", "leading", "lagging",
    "breakdown", "by", "wise", "split", "per", "grouped",
})
_PRODUCT_GRAIN_WORDS = frozenset({"which", "list", "each", "per", "top", "best", "title", "wise"})
_DIM_QUERY_SYNONYMS: dict[str, frozenset[str]] = {
    "sku": frozenset({"sku", "skus"}),
}


def _tokens(query: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (query or "").lower())


def query_has_grain_intent(query: str) -> bool:
    words = set(_tokens(query))
    if words & _GRAIN_WORDS or words & {"sku", "skus"}:
        return True
    return bool(words & {"product", "products"} and words & _PRODUCT_GRAIN_WORDS)


def _catalogue_id(mid: str, metrics: Any) -> str:
    defn = metrics.get(mid) if metrics is not None and hasattr(metrics, "get") else None
    return str(getattr(defn, "catalogue_metric", None) or mid)


def _bootstrap_meta(cid: str, metrics: Any, bootstrap: Any) -> Any:
    if bootstrap is None or not hasattr(bootstrap, "get"):
        return None
    return bootstrap.get(cid) or bootstrap.get(_catalogue_id(cid, metrics))


def _is_intraday(cid: str, metrics: Any, bootstrap: Any) -> bool:
    meta = _bootstrap_meta(cid, metrics, bootstrap)
    if meta is not None:
        grain = str((getattr(meta, "raw", None) or {}).get("grain") or "").lower()
        view = str(getattr(meta, "view", None) or "").lower()
        blob = f"{grain} {view}".strip()
        if blob:
            return "hour" in blob or "intraday" in blob
    return is_intraday_id(cid)


def _concept_key(cid: str, metrics: Any) -> str:
    return cadence_stem(_catalogue_id(cid, metrics)).lower()


def collapse_assigned_metrics(
    ids: list[str],
    metrics: Any,
    query: str = "",
    bootstrap: Any = None,
) -> list[str]:
    """One retrieve per concept. Cadence siblings collapse via catalogue grain/view.

    ``metric.ctr`` + ``meta_ctr`` + ``meta_ctr_hourly`` on "what is meta CTR"
    becomes the default (non-intraday) catalogue id. Intraday stays only when
    the question names hourly/intraday, or when no daily sibling was assigned.
    Conjunctions (gross and net, Meta and Google CTR) stay distinct concepts.
    """
    tokens = set(_tokens(query))
    want_intraday = bool(tokens & {"hourly", "intraday"})
    canonical_of = getattr(metrics, "canonical_id", None)
    ordered: list[str] = []
    seen: set[str] = set()
    for mid in ids:
        if not mid:
            continue
        cid = canonical_of(mid) if callable(canonical_of) else mid
        if cid not in seen:
            seen.add(cid)
            ordered.append(cid)
    families: dict[str, list[str]] = {}
    for cid in ordered:
        families.setdefault(_concept_key(cid, metrics), []).append(cid)
    preferred: set[str] = set()
    for group in families.values():
        intra = [m for m in group if _is_intraday(m, metrics, bootstrap)]
        daily = [m for m in group if m not in intra]
        if want_intraday and intra:
            preferred.add(intra[0])
        elif daily:
            preferred.add(daily[0])
        else:
            preferred.add(group[0])
    return _attributed_revenue_hints(query, [cid for cid in ordered if cid in preferred])


def breakdown_from_query(query: str, supported: list[str]) -> list[str]:
    """Pick a supported dim the question actually names. Catalogue ∩ words."""
    supported_set = set(supported)
    words = set(_tokens(query))
    if words & {"sku", "skus"} and "sku" in supported_set:
        return ["sku"]
    if (
        words & {"product", "products"}
        and words & _PRODUCT_GRAIN_WORDS
        and "product_title" in supported_set
    ):
        return ["product_title"]
    if words & {"channel", "channels"}:
        for dim in ("lt_channel", "channel"):
            if dim in supported_set:
                return [dim]
    return []


def _attributed_revenue_hints(query: str, ids: list[str]) -> list[str]:
    """Keep the revenue concept the query actually named -- Shopify net sales
    or attribution-model revenue -- not both families at once.

    A hint list carrying both ("net sales" alias-matched plus "attributed_*"
    catalogue-search noise, or vice versa) later hits ``constrain_hints_to_grain``,
    which keeps whichever family happens to support the resolved grain -- silently
    substituting the *other* concept for the one the user actually asked about
    (e.g. "net sales by channel" resolving to attributed_net_revenue purely
    because attributed_net_revenue declares more catalogue dimensions).
    """
    q = (query or "").lower()
    wants_attributed = "attributed" in q
    wants_net_sale = "net sale" in q
    if wants_attributed and not wants_net_sale:
        return [item for item in ids if not _is_net_sales_id(item)]
    if wants_net_sale and not wants_attributed:
        return [item for item in ids if not _is_attributed_revenue_id(item)]
    return list(ids)


def _is_net_sales_id(item: str) -> bool:
    key = item.replace("-", "_").lower()
    return "net_sales" in key or "commerce_net_revenue" in key


def _is_attributed_revenue_id(item: str) -> bool:
    return "attributed" in item.replace("-", "_").lower()


def _alias_hits(query_tokens: set[str], runtime: SwarmRuntime) -> list[tuple[int, str]]:
    """Glossary synonyms from the metric registry — gs→gross_sales, roas→gross_roas."""
    scored: list[tuple[int, str]] = []
    for metric in runtime.metrics.all():
        for alias in getattr(metric, "aliases", None) or []:
            parts = [p for p in _tokens(alias) if p not in _STOPWORDS]
            if not parts:
                continue
            if set(parts) <= query_tokens:
                scored.append((len(parts) + 10, metric.id))
                break
    return scored


def _collapse_hints(ids: list[str], *, runtime: SwarmRuntime, query: str) -> list[str]:
    return collapse_assigned_metrics(
        ids, runtime.metrics, query, getattr(runtime, "bootstrap", None)
    )


async def hints_from_catalogue(query: str, *, runtime: SwarmRuntime, agent_id: str = "coordinator_agent") -> list[str]:
    """Resolve query language to assigned catalogue ids — one concept, not a sibling dump."""
    resolved = await _resolve_one_term(query, runtime=runtime, agent_id=agent_id)
    if resolved:
        collapsed = _collapse_hints(resolved, runtime=runtime, query=query)
        if collapsed:
            return collapsed

    q_tokens = {tok for tok in _tokens(query) if tok not in _STOPWORDS}
    alias_scored = _alias_hits(q_tokens, runtime)

    catalogue_scored: list[tuple[int, str]] = []
    if "seleric.catalogue_search_metrics" in runtime.mcp.capabilities:
        try:
            result = await runtime.mcp.call(
                agent_id=agent_id,
                capability="seleric.catalogue_search_metrics",
                arguments={"query": query},
            )
        except Exception:
            result = {}
        for match in result.get("matches") or []:
            how = str(match.get("matched_on") or "")
            if how.startswith("description"):
                continue
            registry_id = runtime.metrics.id_for_catalogue(match.get("id"))
            if not registry_id:
                continue
            hay = f"{match.get('id') or ''} {match.get('display_name') or ''}".lower().replace("_", " ")
            hay_tokens = set(_tokens(hay))
            overlap_tokens = hay_tokens & q_tokens
            overlap = len(overlap_tokens)
            cid = str(match.get("id") or "")
            cid_parts = [p for p in cid.split("_") if p]
            if overlap >= _MIN_MULTI_TOKEN_OVERLAP:
                catalogue_scored.append((overlap, registry_id))
            elif overlap == 1:
                tok = next(iter(overlap_tokens))
                # "roas" must match gross_roas / net_roas, not only a metric whose
                # entire id is the single token "roas".
                if tok == cid or cid_parts == [tok] or tok in cid_parts:
                    catalogue_scored.append((overlap, registry_id))

    scored = alias_scored + catalogue_scored
    if not scored:
        # Token-overlap search misses single strong terms whose id is a compound
        # (e.g. "roas" vs "net_roas_all_channels" — overlap of 1, not the full id).
        # Fall back to the glossary-backed resolver for exactly that case.
        return _collapse_hints(
            await _resolve_metric_term(query, runtime=runtime, agent_id=agent_id),
            runtime=runtime,
            query=query,
        )
    out: list[str] = []
    best = max(item[0] for item in scored)
    for overlap, registry_id in scored:
        if overlap == best and registry_id not in out:
            out.append(registry_id)
    return _collapse_hints(out, runtime=runtime, query=query)


async def _resolve_one_term(text: str, *, runtime: SwarmRuntime, agent_id: str) -> list[str]:
    if _RESOLVE_TERM_CAP not in runtime.mcp.capabilities or not (text or "").strip():
        return []
    try:
        result = await runtime.mcp.call(
            agent_id=agent_id,
            capability=_RESOLVE_TERM_CAP,
            arguments={"text": text},
        )
    except Exception:
        return []
    if not isinstance(result, dict) or result.get("kind") not in _RESOLVED_TERM_KINDS:
        return []
    metric_id = result.get("metric_id")
    if not metric_id:
        return []
    bootstrap = getattr(runtime, "bootstrap", None)
    if bootstrap is not None:
        await bootstrap.refresh_if_stale()
        runtime.metrics.bind_catalogue(bootstrap)
    registry_id = runtime.metrics.id_for_catalogue(metric_id) or str(metric_id)
    return [registry_id]


async def _resolve_metric_term(query: str, *, runtime: SwarmRuntime, agent_id: str) -> list[str]:
    """Glossary fallback for hints_from_catalogue via catalogue_resolve_term.

    catalogue_resolve_term matches a single business term ("roas"), not a
    full sentence — "how much roas increased over 3 days" comes back
    "unknown". Try each non-stopword token in isolation instead, stopping at
    the first resolved/auto_resolved hit. Never run this on a token like
    "meta" while search already ranked "meta CTR" — the caller only reaches
    here after search scored nothing.
    """
    tokens = [tok for tok in _tokens(query) if tok not in _STOPWORDS and len(tok) >= 3]
    for term in dict.fromkeys(tokens):
        hit = await _resolve_one_term(term, runtime=runtime, agent_id=agent_id)
        if hit:
            return hit
    return []


def _query_tokens(query: str) -> set[str]:
    return {
        tok
        for tok in _tokens((query or "").replace("-", " "))
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
            continue
        synonyms = _DIM_QUERY_SYNONYMS.get(dim, ())
        if synonyms and (q_tokens & set(synonyms)):
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


def ground_live_grain(
    live: list[str],
    hints: list[str],
    *,
    metrics: MetricRegistry,
    bootstrap: CatalogueBootstrap,
) -> list[str]:
    """Keep live catalogue dims only when the already-resolved metric can slice them.

    The resolver has no relevance threshold (``funnel status`` → every ``*_status``
    dimension). Swapping the asked metric for whatever supports that hallucination
    is the bug. Intersection with the hinted metric's ``supported_dimensions`` is
    the corroboration — not a keyword table.

    No hints = no metric to invent. Grain stays for Observer to report
    GRAIN_UNSUPPORTED; we do not pick a different measure that happens to
    support the dim.
    """
    ordered = list(dict.fromkeys(d for d in live if d and not _is_time_dimension(d)))
    if not ordered:
        return []
    if not hints:
        return ordered
    allowed: set[str] = set()
    for hint in hints:
        allowed.update(_supported_for_hint(hint, metrics, bootstrap))
    if not allowed:
        return []
    return [d for d in ordered if d in allowed]


async def resolve_grain_texts(texts: list[str], *, runtime: SwarmRuntime) -> list[str]:
    """Union of catalogue_resolve_dimension hits for the query and LLM entities."""
    out: list[str] = []
    seen: set[str] = set()
    for text in texts:
        raw = str(text or "").strip()
        if not raw or raw.startswith("metric."):
            continue
        for dim in await resolve_catalogue_dimension(raw, runtime=runtime):
            if dim not in seen:
                seen.add(dim)
                out.append(dim)
    return out


async def resolve_catalogue_dimension(query: str, *, runtime: SwarmRuntime) -> list[str]:
    """Dimension ids from catalogue_resolve_dimension (or typed resolve_term).

    Untyped metric-only resolve_term payloads are ignored. Ambiguous payloads
    return every candidate id; callers ground those against the hinted metric.
    """
    mcp = getattr(runtime, "mcp", None)
    if mcp is None:
        return []
    attempts: list[tuple[str, dict[str, Any]]] = []
    caps: set[str] = set(getattr(mcp, "capabilities", set()) or ())
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
        except Exception:  # noqa: S112 - dimension resolve soft-fail; try next capability
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
    """Filter assigned hints to those that can slice the named grain.

    Does not replace the asked measure with a sibling that supports the dim.
    Returns (hints, resolved_dimensions). No-op when bootstrap is cold.
    """
    if bootstrap is None or not bootstrap.is_warm():
        return list(hints), []
    grain = [d for d in (resolved_grain or []) if d]
    if not grain:
        grain = dimensions_in_query(query, bootstrap.dimension_ids(), bootstrap.alias_index())
    known_dims = bootstrap.dimension_ids()
    if not grain:
        grain = breakdown_from_query(query, list(known_dims))
    if not grain:
        return list(hints), []

    # Scoped to the metrics this query actually hinted, not every metric in
    # the registry. Scanning metrics.all() let an unrelated metric's grain
    # (e.g. channel_orders' plain "channel") outrank the *asked* metric's own
    # dimension (e.g. attributed_net_revenue's "lt_channel") purely because it
    # existed somewhere else in the registry -- ambiguous grain then resolved
    # to a dimension the asked metric can't actually slice by, and the fetch
    # silently returned "no data available" instead of the real breakdown.
    registry_supported: set[str] = set()
    for hint in hints:
        registry_supported.update(_supported_for_hint(hint, metrics, bootstrap))

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
    return list(hints), resolved


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


def validate_dimensions_for_metric(
    dimensions: list[str],
    metric_id: str | None,
    metrics: MetricRegistry,
) -> list[str]:
    """Keep only the LLM's directly-picked dimensions the resolved metric
    actually declares (Phase 1's ``SwarmClassificationV1.dimensions``) —
    swarm_v2's replacement for grounding a dimension by re-deriving it from
    query text (``dimensions_in_query``/``apply_catalogue_grain``).

    No declared ``supported_dimensions`` on the metric (still true for many
    YAML-overlay-only entries) means no information to validate against —
    trust the LLM's picks rather than silently dropping every breakdown.
    """
    if not dimensions or not metric_id:
        return list(dict.fromkeys(dimensions))
    definition = metrics.get(metric_id)
    if definition is None:
        return list(dict.fromkeys(dimensions))
    supported = set((getattr(definition, "raw", None) or {}).get("supported_dimensions") or [])
    if not supported:
        return list(dict.fromkeys(dimensions))
    return [d for d in dict.fromkeys(dimensions) if d in supported]


async def apply_catalogue_grain(
    query: str,
    hints: list[str],
    *,
    runtime: SwarmRuntime,
    entities: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Resolve grain from the live catalogue, then keep dims the hinted metric can slice.

    Ambiguous questions are not keyword-matched. ``catalogue_resolve_dimension``
    returns resolved | ambiguous | unknown; we intersect candidates with the
    asked metric's supported_dimensions. Hallucinated dims (status →
    fulfillment_status on a sessions question) drop out. Remaining ties go
    through ontology ``grain_defaults`` via constrain_hints/pick_grain.
    """
    bootstrap = getattr(runtime, "bootstrap", None)
    if bootstrap is None:
        return list(hints), []
    if not query_has_grain_intent(query):
        return list(hints), []
    await bootstrap.refresh_if_stale()
    runtime.metrics.bind_catalogue(bootstrap)
    grain_texts = [query, *(entities or [])]
    live = await resolve_grain_texts(grain_texts, runtime=runtime)
    grounded = ground_live_grain(
        live, hints, metrics=runtime.metrics, bootstrap=bootstrap
    )
    return constrain_hints_to_grain(
        hints,
        query=query,
        metrics=runtime.metrics,
        bootstrap=bootstrap,
        resolved_grain=grounded or None,
    )
