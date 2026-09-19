"""Live-catalogue grounding for LLM classifications — shared by lookup_v1 and
swarm_v2 so metric/entity hints are never a local phrase table, in either
pipeline.

2026-09-18: the actual bug #8 mechanism — free-text query tokens matched
against dimension names (``dimensions_in_query``/``_GENERIC_DIM_TOKENS``)
and the catalogue-search/alias-based metric guesser
(``hints_from_catalogue``) and its grain-grounding wrapper
(``apply_catalogue_grain``/``ground_live_grain``/``constrain_hints_to_grain``)
— is deleted (see docs/refactor/TASK_SHEET.md, Profile B). What remains is
not a local heuristic in
that sense: ``collapse_assigned_metrics``/``validate_dimensions_for_metric``
operate on already-resolved catalogue metadata (no query-vs-name keyword
matching), and ``resolve_catalogue_dimension`` calls the live
``seleric-mcp`` resolver directly. ``breakdown_from_query``/``pick_grain``/
``query_has_grain_intent`` remain as the still-live
``agents/intelligence/observer.py`` grain-resolution path's own gate/
tie-breaker, layered on top of that live resolver, not a replacement for
it — retiring that whole path is tracked separately (Sprint 5, old-pipeline
deletion) once its owner is confirmed.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from seleric_swarm.services.metrics import cadence_stem, is_intraday_id

if TYPE_CHECKING:
    from seleric_swarm.runtime import SwarmRuntime
    from seleric_swarm.services.metrics import MetricRegistry

_STOPWORDS = {
    "what", "is", "the", "a", "an", "for", "on", "of", "and", "were", "was",
    "how", "many", "today", "yesterday", "last", "days", "in", "to",
    "get", "me", "report", "wise", "breakdown",
}

_MIN_DIM_TOKEN = 3
_RESOLVE_DIM_CAP = "seleric.catalogue_resolve_dimension"
_RESOLVE_TERM_CAP = "seleric.catalogue_resolve_term"
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
    catalogue-search noise, or vice versa) risks a downstream consumer
    silently substituting the *other* concept for the one the user actually
    asked about.
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


def _query_tokens(query: str) -> set[str]:
    return {
        tok
        for tok in _tokens((query or "").replace("-", " "))
        if tok not in _STOPWORDS and len(tok) >= _MIN_DIM_TOKEN
    }


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


def query_tokens_for_grain(query: str) -> list[str]:
    """Non-stopword tokens (len>=3), in order, deduped — per-token fallback
    for when a whole-sentence ``resolve_catalogue_dimension`` call resolves
    to nothing (e.g. "units sold by product" as one string vs. "product"
    alone). Same idea as the deleted ``_resolve_metric_term()``'s per-token
    retry, kept here since ``resolve_catalogue_dimension`` is the live-only
    replacement for that whole heuristic chain."""
    out: list[str] = []
    for tok in _tokens(query):
        if tok in _STOPWORDS or len(tok) < _MIN_DIM_TOKEN:
            continue
        if tok not in out:
            out.append(tok)
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
    query text (the deleted ``dimensions_in_query``/``apply_catalogue_grain``).

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
