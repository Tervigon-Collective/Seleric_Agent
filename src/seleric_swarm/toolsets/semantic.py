"""SemanticToolset v0 — the only path to numeric business data (rule 5).

Thin wrappers over live ``seleric-mcp`` catalogue/metrics tools via the
already-live ``MCPGateway`` (``protocols/mcp/gateway.py``) and its generic
arg-builder (``services/mcp_query.py``) — both reused as-is, not rebuilt.

``metric_id`` is whatever the caller states, used verbatim all the way to
``metrics_query``/``metrics_drilldown`` — no local alias table, no legacy
metric-registry lookup, no keyword-overlap resolver (rule 1). See
``_AGENT_ID``'s comment below for why: a same-day merge briefly reintroduced
an exact-alias overlay here, which collapsed Profile B's bug #2 regression
test (two spellings of one metric must each produce their own
independently-attributed evidence).
"""

from __future__ import annotations

import calendar
import difflib
import json
import re
from datetime import datetime, timedelta
from typing import Any

from pydantic_ai import ModelRetry, RunContext

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.services.mcp_query import (
    _BRAND_DIM_KEYS,
    build_metrics_query_args,
    call_metrics_query,
    dimension_value,
    row_date,
)
from seleric_swarm.toolsets import catalogue_index

# LLM placeholders that are not real catalogue values. ``query_metrics``
# requires a ``dimensions`` dict in the frozen signature, so models fill it
# with "some_brand" / "example" when the user never named a brand (live
# 2026-09-19: "gross sale" → unknown brand 'some_brand').
_PLACEHOLDER_DIM_VALUES = frozenset(
    {
        "acme",
        "bar",
        "baz",
        "brand",
        "brand_id",
        "brand_name",
        "dummy",
        "example",
        "example_brand",
        "foo",
        "my_brand",
        "n/a",
        "na",
        "none",
        "null",
        "placeholder",
        "sample",
        "some_brand",
        "somebrand",
        "string",
        "test",
        "unknown",
        "your_brand",
    }
)


def _is_placeholder_dimension_value(value: str) -> bool:
    text = value.strip().lower().replace("-", "_").replace(" ", "_")
    if not text or text in _PLACEHOLDER_DIM_VALUES:
        return True
    return text.startswith(("some_", "example_", "sample_", "dummy_", "test_"))


def _normalize_dim_token(text: str) -> str:
    return text.strip().lower().replace("-", "_").replace(" ", "_")


def _is_self_referential_dimension_value(key: str, value: str) -> bool:
    """LLM echoed the dimension name back as its own value (live
    2026-09-21: ``dimensions={"product_title": "product_title"}`` when the
    caller wanted a breakdown by product, not a literal filter for a
    product named after its own column) — that's "give me no value", i.e.
    a group-by, not a filter that can never match a real row."""
    return _normalize_dim_token(value) == _normalize_dim_token(key)


# The model reaches for a SQL ``GROUP BY *`` idiom — ``{"commerce_order_id":
# "*"}`` — to mean "break this down", but the tool contract expresses a
# breakdown as an EMPTY value (a truthy value is a filter). Cube then tries
# ``commerce_order_id = "*"`` and fails on the type mismatch (live 2026-09-22
# MS3: an Int64 id compared to the string "*"). Treat these wildcard tokens as
# the group-by signal the model meant.
_GROUPBY_MARKERS = frozenset({"*", "all", "any", "each", "every", "group_by", "groupby"})


def _sanitize_dimensions(dimensions: dict[str, str] | None) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, raw in (dimensions or {}).items():
        if raw is None:
            cleaned[str(key)] = ""
            continue
        text = str(raw).strip()
        if text and _normalize_dim_token(text) in _GROUPBY_MARKERS:
            cleaned[str(key)] = ""  # breakdown, not a literal filter
            continue
        if text and _is_self_referential_dimension_value(key, text):
            cleaned[str(key)] = ""
            continue
        if text and _is_placeholder_dimension_value(text):
            continue
        cleaned[str(key)] = text
    return cleaned


# Brand filter keys live in mcp_query (the arg builder that injects the default
# brand). Only a brand is safe to auto-drop on an unresolved-value error: the
# builder re-injects the default brand, so a bad brand degrades to the default
# rather than failing the mission. A NON-brand filter is never dropped — silently
# erasing a user-supplied product/return/region filter answers a different
# question (live Suspender-Boots trace).


def _unknown_brand_error(error: object) -> bool:
    text = str(error).lower()
    return "unknown brand" in text or "invalid brand" in text


def _unknown_dimension_error(error: object) -> bool:
    text = str(error).lower()
    return "unknown dimension" in text or _unknown_brand_error(error)


# Single agent identity for MCPGateway allowlisting — the V3 runtime has one
# agent loop (see docs/refactor/01_PROFILE_RUNTIME.md). Write/actions stay
# gated on this id in MCPGateway._authorize; reads accept any caller.
#
# No local alias/registry-based metric-id resolution here, deliberately:
# `metric_id` is used exactly as given by the caller, verbatim, all the way
# to `metrics_query`/`metrics_drilldown` (rule 1) — Profile B's own bug #2
# regression guard (`tests/unit/test_semantic_toolset_bug_regressions.py`)
# requires that two spellings of the same metric each reach Cube unchanged
# and produce independently-attributed evidence, with no MetricRegistry (or
# anything else) in this module canonicalizing one into the other. A
# same-day merge briefly reintroduced an exact-alias overlay here; removed
# 2026-09-19 after it collapsed that regression test back to failing.
_AGENT_ID = "v3_agent"


def _cache_key(capability: str, arguments: dict[str, Any]) -> str:
    """Deterministic key for ``SelericDeps.query_cache`` — same capability +
    same arguments always means the same fetch, so this is the one place
    that decides what "identical query" means for dedup purposes."""
    return f"{capability}:{json.dumps(arguments, sort_keys=True, default=str)}"


_QUERY_CACHE_ENABLED = True

LIVE_DATA_UNAVAILABLE = "live_data_unavailable"
_MAX_DEFINITION_LOOKUPS = 4


def _definition_budget_spent(ctx: RunContext[SelericDeps]) -> ToolResult | None:
    """A soft stop, not an error: the model has enough definitions and looping
    on more (live: "what is CAC?" made 15+ lookups in 191s) only burns time."""
    used = ctx.deps.call_counts.get("definition_lookups", 0) + 1
    ctx.deps.call_counts["definition_lookups"] = used
    if used <= _MAX_DEFINITION_LOOKUPS:
        return None
    return ToolResult(
        success=True,
        summary=(
            "Definition lookups are exhausted for this mission. Answer now from the "
            "definitions you already retrieved; do not call another definition tool."
        ),
    )


async def _cached_metrics_query(
    ctx: RunContext[SelericDeps], arguments: dict[str, Any]
) -> dict[str, Any]:
    fetch = lambda: call_metrics_query(ctx.deps.mcp_client, agent_id=_AGENT_ID, arguments=arguments)
    key = _cache_key("seleric.metrics_query", arguments)
    if _QUERY_CACHE_ENABLED and ctx.deps.query_cache.peek(key) is not None:
        # Already fetched this mission -- a cache hit costs no real Cube
        # query, so it must not consume max_cube_queries (ExecutionLimits,
        # CONTRACTS.md) either.
        return await ctx.deps.query_cache.get_or_fetch(key, fetch)
    verdict = ctx.deps.budget.consume("cube_queries")
    if not verdict.ok:
        raise ModelRetry(
            f"Cube query budget exhausted for this mission ({verdict.reason}). "
            "Do not fetch any more data -- write your final_response now using "
            "the evidence you already have, and say plainly if that isn't enough."
        )
    result = await fetch() if not _QUERY_CACHE_ENABLED else await ctx.deps.query_cache.get_or_fetch(key, fetch)
    if str(result.get("error") or "").startswith("NotImplementedError"):
        # Deployment state, not a transient fault: `prepare_tools` (agent.py)
        # withdraws every data-fetching tool for the rest of the mission so the
        # model cannot keep probing a backend that can never answer.
        ctx.deps.call_counts[LIVE_DATA_UNAVAILABLE] = 1
    return result


def _mcp_error_result(exc: Exception) -> ToolResult:
    if isinstance(exc, NotImplementedError):
        # "Capability not available" is deployment state, not a transient fault:
        # retrying (or trying other metrics) can never succeed, so say so.
        return ToolResult(
            success=False,
            summary=(
                "Live metric data is not connected in this deployment (MCP capability "
                "unavailable). Do not retry or try other metrics: answer from the "
                "catalogue only and say plainly that the numbers could not be fetched."
            ),
            error_code="MCP_UNAVAILABLE",
            retryable=False,
        )
    return ToolResult(
        success=False,
        summary=f"{type(exc).__name__}: {exc}",
        error_code="MCP_UNAVAILABLE",
        retryable=True,
    )


def _fetch_failure(what: str, error: Any) -> ToolResult:
    """A failed Cube fetch. ``call_metrics_query`` flattens exceptions to
    ``"<Type>: <msg>"``, so an unconfigured MCP is recognised by its type name."""
    text = str(error)
    if text.startswith("NotImplementedError"):
        return _mcp_error_result(NotImplementedError(text))
    return ToolResult(
        success=False,
        summary=f"{what} failed: {text}",
        error_code="INSUFFICIENT_EVIDENCE",
        retryable=True,
    )


async def raw_query_metric(
    mcp_client: Any,
    *,
    agent_id: str,
    metric_id: str,
    start: str,
    end: str,
    grain: str | None = None,
    dimensions: list[str] | None = None,
    filters: list[dict[str, Any]] | None = None,
    limit: int | None = None,
    sort: list[dict[str, Any]] | None = None,
    compare_period: str | None = None,
    module: Any = ...,
) -> dict[str, Any]:
    """The one MCP call every fetch path in this repo goes through.

    Sprint 2 consolidation (docs/refactor/SPRINT_PLAN.md): ``metric_id`` is
    used as the ``measures`` value directly — no ``MetricRegistry``/
    ``resolve_measure`` keyword-overlap fallback anywhere in this function.
    Callers that only have a legacy ``metric.xxx`` id resolve it to a
    catalogue id via ``MetricDefinition.catalogue_metric`` (a static config
    field, not a heuristic search) before calling this. ``agent_id`` stays a
    caller-supplied parameter (not the fixed ``_AGENT_ID`` below) so legacy
    callers keep their existing ``MCPGateway`` module-scoping identity.
    """
    args = build_metrics_query_args(
        measure=metric_id,
        start=start,
        end=end,
        grain=grain,
        dimensions=dimensions,
        filters=filters,
        limit=limit,
        sort=sort,
        compare_period=compare_period,
        module=module,
    )
    return await call_metrics_query(mcp_client, agent_id=agent_id, arguments=args)


async def query_metric_series(
    mcp_client: Any,
    *,
    agent_id: str,
    jobs: list[tuple[str, str, Any]],
    time_range: dict[str, Any],
    max_days: int = 60,
    min_rows: int = 8,
    concurrency: int = 8,
) -> Any:
    """Daily multi-metric frame for DoWhy (extracted from former Hybrid.fetch_series).

    ``jobs`` is ``(column_id, catalogue_measure, module_or_ellipsis)`` —
    catalogue measure only, no heuristic resolve. Returns a pandas DataFrame
    indexed by date, or ``None`` if the window is too short/long/sparse.
    """
    import asyncio
    from datetime import date

    import pandas as pd

    start_s = str(time_range.get("start") or time_range.get("end") or "")[:10]
    end_s = str(time_range.get("end") or time_range.get("start") or "")[:10]
    if not start_s or not end_s:
        return None
    try:
        start = date.fromisoformat(start_s)
        end = date.fromisoformat(end_s)
    except ValueError:
        return None
    if end < start:
        start, end = end, start
    n_days = (end - start).days + 1
    if n_days < min_rows or n_days > max_days:
        return None

    sem = asyncio.Semaphore(concurrency)

    async def _one(column_id: str, measure: str, module: Any) -> tuple[str, dict[str, float]]:
        async with sem:
            result = await raw_query_metric(
                mcp_client,
                agent_id=agent_id,
                metric_id=measure,
                start=start.isoformat(),
                end=end.isoformat(),
                grain="day",
                module=module,
            )
        day_values: dict[str, float] = {}
        if result.get("error"):
            return column_id, day_values
        for row in result.get("rows") or []:
            ts = row_date(row)
            raw = row.get(measure)
            if ts is None or raw is None:
                continue
            day_values[ts] = float(raw)
        return column_id, day_values

    gathered = await asyncio.gather(*[_one(*job) for job in jobs]) if jobs else []
    columns: dict[str, dict[str, float]] = {
        column_id: day_values
        for column_id, day_values in gathered
        if len(day_values) >= min_rows
    }
    if not columns:
        return None
    frame = pd.DataFrame(columns)
    frame = frame.dropna(how="any")
    if len(frame) < min_rows:
        return None
    return frame


# Cap on the shortlist handed back to the model. The live glossary search
# fans out to ~40 near-synonyms for a term like "net sales"; the canonical
# glossary hit is always ranked first, so a small window keeps that hit plus a
# few alternatives to disambiguate without paying to echo the whole catalogue.
_SEARCH_SHORTLIST = 8

_SHORTLIST_FIELDS = ("id", "display_name", "view", "supported_dimensions", "matched_on")

# After this many searches in one mission, search_semantics stops returning a
# fresh-looking result and forces the model to commit — a mechanical breaker for
# the paraphrase-search loop (SEARCH-01) that exact-arg caching can't catch.
_MAX_SEARCHES = 3

# Cap the per-row values echoed into the tool summary. Top-N already limits
# rows; this bounds a large ungrouped breakdown. Every row still lands in
# evidence + source_metadata["series"]; the summary just shows the first N.
_MAX_SERIES_IN_SUMMARY = 40


def _slim_match(match: dict[str, Any]) -> dict[str, Any]:
    return {k: match[k] for k in _SHORTLIST_FIELDS if k in match}


def _norm_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _hoist_exact(query: str, matches: list[dict[str, Any]], catalogue: Any) -> list[dict[str, Any]]:
    """Exact metric-id/name match wins over vector rank (live 2026-09-22
    MS3-53296c1a5e: searching the literal id ``product_net_revenue`` ranked it
    31st of 44 — behind glossary 'revenue' hits — so the 8-item shortlist cut
    it and the model re-searched 11×). Move an exact id/display-name hit to the
    front; if the server didn't return it at all but it's a real catalogue id,
    synthesize it from the warmed snapshot so it can't be truncated away."""
    qn = _norm_key(query)
    if not qn:
        return matches
    for i, m in enumerate(matches):
        if _norm_key(m.get("id", "")) == qn or _norm_key(m.get("display_name", "")) == qn:
            return [m, *matches[:i], *matches[i + 1 :]] if i else matches
    for meta in getattr(catalogue, "metrics", ()):  # not in server matches — snapshot fallback
        if _norm_key(meta.id) == qn:
            return [
                {
                    "id": meta.id,
                    "display_name": meta.label or meta.id,
                    "supported_dimensions": list(meta.supported_dimensions or []),
                    "matched_on": "exact_id",
                },
                *matches,
            ]
    return matches


async def search_semantics(ctx: RunContext[SelericDeps], query: str) -> ToolResult:
    """Resolve business language to catalogue metric ids via the live
    glossary-backed catalogue search (``catalogue_search_metrics``): a known
    term (e.g. "topline", "MER") comes back with its canonical id ranked first
    plus a few alternatives to disambiguate near-duplicate siblings. Falls back
    to the local Qdrant index (``toolsets/catalogue_index.py``) if the live
    search is unreachable. Search only — ``get_metric_definition(s)`` /
    ``query_metrics`` still validate against Cube unchanged (rule 1)."""
    count = ctx.deps.call_counts.get("search_semantics", 0) + 1
    ctx.deps.call_counts["search_semantics"] = count
    # Hard budget: past the cap, search_semantics is disabled for the mission —
    # a ModelRetry redirect, not an advisory string the model can ignore (live
    # 2026-09-22 MS3-53296c1a5e: the soft "STOP SEARCHING" summary was ignored
    # 11 times until the step budget tripped). The model already has candidates
    # from earlier searches; force it to execute or report no compatible metric.
    if count > _MAX_SEARCHES:
        raise ModelRetry(
            "SEMANTIC_RESOLUTION_LOOP: search_semantics is disabled for this "
            "mission (budget exhausted). Do NOT call it again. Call query_metrics "
            "with the best metric id from your earlier search results — for a "
            "product/SKU question use a product_* metric (e.g. product_net_revenue, "
            "product_return_revenue, returned_units). If no metric supports the "
            "breakdown you need, call final_result stating that plainly."
        )
    try:
        result = await ctx.deps.mcp_client.call(
            agent_id=_AGENT_ID,
            capability="seleric.catalogue_search_metrics",
            arguments={"query": query},
        )
        hoisted = _hoist_exact(query, list((result or {}).get("matches") or []), ctx.deps.catalogue)
        matches = [_slim_match(m) for m in hoisted][:_SEARCH_SHORTLIST]
        warnings = [] if matches else [f"no catalogue match for '{query}'"]
        return ToolResult(
            success=True,
            summary=f"{len(matches)} metric(s) matched '{query}'",
            warnings=warnings,
            provenance=ArtifactProvenance(source_metadata={"matches": matches}),
        )
    except Exception:
        # Live search down — degrade to the local Qdrant shortlist rather than
        # failing resolution outright (never raise across the tool boundary).
        try:
            matches = catalogue_index.search(query, kind="metric")
        except Exception as exc:
            return _mcp_error_result(exc)
        warnings = [] if matches else [f"no catalogue match for '{query}'"]
        if any(m.get("stale") for m in matches):
            warnings.append("catalogue index may be stale; rerun scripts/sync_catalogue_to_qdrant.py")
        return ToolResult(
            success=True,
            summary=f"{len(matches)} metric(s) matched '{query}' (local index)",
            warnings=warnings,
            provenance=ArtifactProvenance(source_metadata={"matches": matches}),
        )


async def resolve_brand(ctx: RunContext[SelericDeps], name: str) -> ToolResult:
    """Resolve a brand name/code (e.g. "Sniff Theory", "Urthend") to a
    ``brand_id`` for use in a ``query_metrics`` filter — call this instead of
    inventing a brand id when the user names a brand other than the default.

    Returns the resolved ``brand_id`` and canonical name; a partially-loaded
    tenant's ``scope_note`` is surfaced as a warning so a P&L answer isn't given
    for a brand whose revenue side isn't in the warehouse. Resolution only —
    the ``brand_id`` is passed verbatim into a ``filters`` entry
    (``{"dimension": "brand_id", "operator": "equals", "values": [brand_id]}``),
    never used to rewrite a ``metric_id`` (rule 1)."""
    try:
        result = await ctx.deps.mcp_client.call(
            agent_id=_AGENT_ID,
            capability="seleric.catalogue_resolve_brand",
            arguments={"text": name},
        )
    except Exception as exc:
        return _mcp_error_result(exc)
    result = dict(result or {})
    brand_id = result.get("brand_id")
    if not brand_id:
        # Ambiguous/unknown: hand the model whatever the server offered
        # (candidates/suggestions) so it can disambiguate, never a guess.
        return ToolResult(
            success=False,
            summary=f"could not resolve a brand from '{name}'",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
            provenance=ArtifactProvenance(source_metadata=result),
        )
    warnings = [str(result["scope_note"])] if result.get("scope_note") else []
    return ToolResult(
        success=True,
        summary=f"{name} -> brand_id={brand_id} ({result.get('name') or ''})".strip(),
        warnings=warnings,
        provenance=ArtifactProvenance(source_metadata=result),
    )


def _reject_unknown_metric(ctx: RunContext[SelericDeps], metric_id: str) -> None:
    """Raise ``ModelRetry`` with candidate ids when *metric_id* isn't in the
    warmed catalogue snapshot. Validation only — never rewrites the id to a
    guess (rule 1 / the no-alias-table warning in this module's docstring):
    it hands the model the closest catalogue ids and lets it re-pick. Skipped
    when the snapshot is empty (fail-open) or the id is present.
    """
    catalogue = ctx.deps.catalogue
    if not catalogue.metrics or catalogue.has_metric(metric_id):
        return
    candidates = catalogue.closest_metric_ids(metric_id)
    if not candidates:
        return
    raise ModelRetry(
        f"'{metric_id}' is not a catalogue metric id. Closest ids: "
        f"{', '.join(candidates)}. Pick the exact id from the [catalogue] "
        f"listing and retry."
    )


def _reject_incompatible_dimensions(
    ctx: RunContext[SelericDeps], metric_id: str, dimensions: dict[str, str]
) -> None:
    """Fail fast when *metric_id* can't carry a requested dimension, pointing at
    metrics that can (live 2026-09-22 MS3: "top returned products" tried to
    break the order-grain ``refunded_orders`` down by ``product_title`` — an
    incompatible pairing — and wandered through metric after metric instead of
    switching to a product-grain one). Deterministic redirect, not a rewrite:
    the model still re-picks the id (rule 1).

    Fail-open: skipped when the snapshot is empty, the metric carries no
    ``supported_dimensions`` in the snapshot, or nothing else supports the
    dimension either (a real capability gap Cube should answer, not a bad pick).
    """
    catalogue = ctx.deps.catalogue
    if not catalogue.metrics:
        return
    supported = set(catalogue.supported_dimensions_for(metric_id))
    if not supported:
        return  # snapshot doesn't describe this metric's dims — let Cube decide
    for key in dimensions:
        if key in supported:
            continue
        alternatives = [m for m in catalogue.metrics_supporting_dimension(key) if m != metric_id]
        if not alternatives:
            continue  # nothing supports it — not a wrong-pick, don't block
        raise ModelRetry(
            f"'{metric_id}' does not support the '{key}' dimension (it supports: "
            f"{', '.join(sorted(supported))}). For a breakdown/filter by '{key}', "
            f"use one of these metrics instead: {', '.join(alternatives)}. "
            f"Re-resolve and retry with a compatible metric."
        )


async def get_metric_definition(ctx: RunContext[SelericDeps], metric_id: str) -> ToolResult:
    """Fetch one metric's full catalogue definition (catalogue_get_metric)."""
    if (spent := _definition_budget_spent(ctx)) is not None:
        return spent
    _reject_unknown_metric(ctx, metric_id)
    try:
        result = await ctx.deps.mcp_client.call(
            agent_id=_AGENT_ID, capability="seleric.catalogue_get_metric", arguments={"metric_id": metric_id}
        )
    except Exception as exc:
        return _mcp_error_result(exc)
    if not result or result.get("error"):
        return ToolResult(
            success=False,
            summary=f"metric '{metric_id}' not found in live catalogue",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
        )
    return ToolResult(
        success=True,
        summary=f"definition for {metric_id}",
        provenance=ArtifactProvenance(source_metadata={"definition": result}),
    )


async def get_metric_definitions(ctx: RunContext[SelericDeps], metric_ids: list[str]) -> ToolResult:
    """Batch full catalogue definitions (incl. ``supported_dimensions``) for several metric ids at once.

    One ``catalogue_get_metrics`` call instead of N ``get_metric_definition``
    calls — use it after shortlisting candidates (e.g. from ``search_semantics``)
    to pull the dims/definitions a complex question or drilldown needs in a
    single round trip. Partial success: unknown ids come back as warnings with
    the valid ones still returned; the id is never rewritten to a guess (rule 1).
    """
    if (spent := _definition_budget_spent(ctx)) is not None:
        return spent
    ids = [str(m).strip() for m in (metric_ids or []) if str(m).strip()]
    if not ids:
        return ToolResult(
            success=False,
            summary="no metric ids given",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
        )
    try:
        result = await ctx.deps.mcp_client.call(
            agent_id=_AGENT_ID,
            capability="seleric.catalogue_get_metrics",
            arguments={"metric_ids": ids},
        )
    except Exception as exc:
        return _mcp_error_result(exc)
    definitions = (result or {}).get("metrics") or {}
    errors = (result or {}).get("errors") or {}
    if not definitions:
        return ToolResult(
            success=False,
            summary=f"no catalogue definitions found for {ids}",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
        )
    warnings = [f"unknown metric id '{mid}'" for mid in errors]
    return ToolResult(
        success=True,
        summary=f"definitions for {len(definitions)} metric(s)",
        warnings=warnings,
        provenance=ArtifactProvenance(source_metadata={"definitions": definitions, "errors": errors}),
    )


def _bucket_end(bucket_start: datetime, grain: str) -> datetime:
    """Inclusive end of one Cube bucket. Day is a single day (start == end)."""
    if grain == "week":
        return bucket_start + timedelta(days=6)
    if grain == "month":
        last = calendar.monthrange(bucket_start.year, bucket_start.month)[1]
        return bucket_start.replace(day=last)
    return bucket_start


def _top_n_sort(metric_id: str, order: str | None) -> list[dict[str, Any]] | None:
    """Sort spec for a top/bottom-N query: rank rows by the metric value.

    ``order`` is "desc" (top/highest/most) or "asc" (bottom/lowest/least). The
    live ``metrics_query`` SortSpec shape is ``{"field", "direction"}`` (probed
    2026-09-22). Any other value means "no explicit ranking"."""
    if order not in ("desc", "asc"):
        return None
    return [{"field": metric_id, "direction": order}]


# Cap on values enumerated when disambiguating a zero-row filter. A high-card
# dimension (thousands of SKUs) is bounded here so the probe can't blow up; the
# close-match is still found among the top slice.
_MAX_VALUE_PROBE = 200


async def _suggest_close_values(
    ctx: RunContext[SelericDeps],
    *,
    metric_id: str,
    start: str,
    end: str,
    filters: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """A zero-row exact filter is ambiguous: the value may be misspelled/absent
    rather than genuinely empty (live Suspender-Boots: ``product_title=
    "Suspender Boots"`` → 0 rows, catalogue holds "Suspender Boot"). Re-run the
    SAME metric grouped by the filtered dimension(s) to list the values that
    actually exist, then fuzzy-match each requested value against them.

    Generic on purpose — no metric- or dimension-specific branch, no hardcoded
    product/SKU logic: any equals-filter that returns nothing gets the same
    "did you mean" treatment via the dimensions the metric already supports and
    stdlib ``difflib``.
    """
    dims = [f["dimension"] for f in filters]
    probe_args = build_metrics_query_args(
        measure=metric_id, start=start, end=end, dimensions=dims, limit=_MAX_VALUE_PROBE
    )
    result = await _cached_metrics_query(ctx, probe_args)
    if result.get("error"):
        return {}
    rows = result.get("rows") or []
    suggestions: dict[str, list[str]] = {}
    for f in filters:
        dim = f["dimension"]
        wanted = str((f.get("values") or [""])[0])
        existing = sorted({str(dimension_value(row, dim)) for row in rows} - {"None", ""})
        close = difflib.get_close_matches(wanted, existing, n=3, cutoff=0.6)
        if close:
            suggestions[dim] = close
    return suggestions


async def query_metrics(
    ctx: RunContext[SelericDeps],
    metric_id: str,
    dimensions: dict[str, str] | None = None,
    grain: str = "none",
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    order: str | None = None,
    limit: int | None = None,
) -> ToolResult:
    """The only path to a numeric metric value. Writes one EvidenceArtifact.

    ``dimensions`` / periods default empty-or-as_of so the model can look up
    "gross sale" without inventing a brand filter or a training-data year.

    For a top/bottom-N ranking, break down by the entity dimension (empty
    value, e.g. ``dimensions={"product_title": ""}``), set ``order="desc"``
    (top/most/highest) or ``"asc"`` (bottom/least/lowest), and ``limit=N``.
    That is one call — do not fetch every row and sort client-side.
    """
    _reject_unknown_metric(ctx, metric_id)
    dimensions = _sanitize_dimensions(dimensions)
    _reject_incompatible_dimensions(ctx, metric_id, dimensions)
    period_end = period_end or ctx.deps.as_of
    period_start = period_start or period_end
    breakdown = [k for k, v in dimensions.items() if not v]
    filters = [
        {"dimension": k, "operator": "equals", "values": [v]} for k, v in dimensions.items() if v
    ]
    sort = _top_n_sort(metric_id, order)
    # Only scope to the default brand for metrics that actually carry a brand
    # dimension (catalogue-driven, not a hardcoded metric list): injecting a
    # brand filter onto a brand-less metric would make Cube reject the query.
    # Fail-open when the snapshot is empty.
    supports_brand = (not ctx.deps.catalogue.metrics) or bool(
        {d.lower() for d in ctx.deps.catalogue.supported_dimensions_for(metric_id)} & _BRAND_DIM_KEYS
    )
    args = build_metrics_query_args(
        measure=metric_id,
        start=period_start.date().isoformat(),
        end=period_end.date().isoformat(),
        grain=None if grain == "none" else grain,
        dimensions=breakdown or None,
        filters=filters or None,
        sort=sort,
        limit=limit,
        inject_default_brand=supports_brand,
    )
    result = await _cached_metrics_query(ctx, args)
    if result.get("error") and _unknown_brand_error(result["error"]):
        # Drop ONLY the user's bad brand filter; the arg builder re-injects the
        # default brand in its place. Keep every other filter and the breakdown.
        # Never strip a user-supplied product/return/region filter — that
        # silently answers a different question and still reports success (live
        # Suspender-Boots trace).
        kept_filters = [
            f for f in filters if _normalize_dim_token(f["dimension"]) not in _BRAND_DIM_KEYS
        ]
        if len(kept_filters) != len(filters):
            filters = kept_filters
            args = build_metrics_query_args(
                measure=metric_id,
                start=period_start.date().isoformat(),
                end=period_end.date().isoformat(),
                grain=None if grain == "none" else grain,
                dimensions=breakdown or None,
                filters=filters or None,
                sort=sort,
                limit=limit,
                inject_default_brand=supports_brand,
            )
            result = await _cached_metrics_query(ctx, args)
    if result.get("error"):
        # An unknown NON-brand dimension is an unsupported request, not a
        # transient failure: surface it plainly so the model reports UNSUPPORTED
        # rather than looping or quietly dropping the constraint.
        if _unknown_dimension_error(result["error"]) and not _unknown_brand_error(result["error"]):
            return ToolResult(
                success=False,
                summary=f"query_metrics({metric_id}) failed: {result['error']}",
                error_code="UNSUPPORTED_QUERY",
                retryable=False,
            )
        return _fetch_failure(f"query_metrics({metric_id})", result["error"])
    rows = result.get("rows") or []
    if not rows:
        # Zero rows on an exact NON-brand filter is ambiguous — the value may be
        # misspelled or absent, not genuinely empty. Enumerate the dimension's
        # real values once and surface the near-matches so the model can correct
        # the value or tell the user, instead of a bare "no data" that hides a
        # typo (live Suspender-Boots trace). Brand filters are excluded (they
        # already fall back to the default above).
        # ponytail: one extra Cube query per zero-row filtered miss; cached by
        # args so a re-issued identical query pays it only once.
        non_brand = [
            f for f in filters if _normalize_dim_token(f["dimension"]) not in _BRAND_DIM_KEYS
        ]
        hint = ""
        if non_brand:
            suggestions = await _suggest_close_values(
                ctx,
                metric_id=metric_id,
                start=period_start.date().isoformat(),
                end=period_end.date().isoformat(),
                filters=non_brand,
            )
            if suggestions:
                did_you_mean = "; ".join(
                    f"{dim} ≈ {', '.join(vals)}" for dim, vals in suggestions.items()
                )
                hint = (
                    f" — the requested value has no rows and may be misspelled or "
                    f"not present. Did you mean: {did_you_mean}? Retry with an exact "
                    f"value, or tell the user it doesn't exist."
                )
        return ToolResult(
            success=False,
            summary=f"no data for {metric_id} over {period_start.date()}..{period_end.date()}{hint}",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
        )
    provenance = ArtifactProvenance(
        query_version=str(result.get("provenance", {}).get("query_id") or ""),
        source_metadata=result.get("provenance") or {},
    )

    async def _write_evidence() -> ToolResult:
        # grain="none" -> Cube returns one period-aggregate row. A real grain
        # ("day"/"week"/"month") -> one row per bucket; each bucket is its own
        # EvidenceArtifact (rule 6/7: one artifact per fetched fact, not a
        # multi-day value folded into a single artifact) — needed so a
        # day-granularity series (e.g. feeding a causal/anomaly consumer) is
        # immutable evidence per day, not one mutable blob.
        per_row_dates = [row_date(row) for row in rows] if grain != "none" else [None] * len(rows)
        currency = str((result.get("provenance") or {}).get("currency") or "").strip()
        artifact_ids: list[str] = []
        last_value: float | None = None
        # The per-row (label, value) series the MODEL sees in the tool return.
        # Without this the tool handed back only a single scalar + opaque
        # artifact ids, so a grain=day / breakdown query starved the model of
        # the very numbers it had to report — and it fabricated plausible ones
        # (live 2026-09-22 MS3-ad0fe7c8a2: 7 daily net-sales values invented,
        # none matching the stored evidence). Return the real values.
        series: list[dict[str, Any]] = []
        for row, bucket_date in zip(rows, per_row_dates, strict=True):
            value = row.get(metric_id)
            if value is None:
                continue
            last_value = float(value)
            bucket_start = datetime.fromisoformat(bucket_date).replace(tzinfo=period_start.tzinfo) if bucket_date else period_start
            # A parsed bucket is that grain's own window, not the query
            # window. Day stays one inclusive day (start == end). Week is
            # seven days and month is the calendar month — analytics grain
            # checks reject a week labelled as a 1-day or multi-week span.
            bucket_end = _bucket_end(bucket_start, grain) if bucket_date else period_end
            # Filters (truthy dimension values) are known up front. A breakdown
            # key (empty value, e.g. dimensions={"product_id": ""}) groups the
            # Cube query, but which group THIS row belongs to only exists in the
            # row itself — read it there (live 2026-09-21: a product_id breakdown
            # via query_metrics wrote every row with dimensions={}, making ~200
            # per-product counts indistinguishable from each other).
            row_dimensions = {k: v for k, v in dimensions.items() if v}
            for key in breakdown:
                row_dimensions[key] = str(dimension_value(row, key))
            evidence = EvidenceArtifact(
                metric_id=metric_id,
                dimensions=row_dimensions,
                grain=grain,  # type: ignore[arg-type]
                as_of=ctx.deps.as_of,
                period_start=bucket_start,
                period_end=bucket_end,
                value=last_value,
                unit=currency or None,
                source_query=args,
            )
            artifact = ctx.deps.artifact_store.put(
                Artifact(
                    workspace_id=ctx.deps.principal.workspace_id,
                    artifact_type="evidence",
                    payload=evidence.model_dump(mode="json"),
                    classification="factual",
                    evidence_ids=[f"raw:{metric_id}:{bucket_start.date()}:{bucket_end.date()}"],
                    provenance=provenance,
                    mission_id=ctx.deps.mission_id,
                )
            )
            artifact_ids.append(artifact.id)
            # Label: the bucket window for a time series, else the breakdown
            # dimension value, else the plain period. Week/month include both
            # ends so two buckets cannot share one label.
            if bucket_date and grain in {"week", "month"}:
                label = f"{bucket_start.date()}..{bucket_end.date()}"
            elif bucket_date:
                label = bucket_date
            elif row_dimensions:
                label = ", ".join(f"{k}={v}" for k, v in row_dimensions.items())
            else:
                label = f"{bucket_start.date()}..{bucket_end.date()}"
            series.append({"label": label, "value": last_value})
        if not artifact_ids:
            return ToolResult(
                success=False,
                summary=f"no usable value for {metric_id} over {period_start.date()}..{period_end.date()}",
                error_code="INSUFFICIENT_EVIDENCE",
                retryable=False,
            )
        # Build the summary the model reads. A single row → the scalar it
        # expects for a lookup. Multiple rows → the actual per-row values, so
        # the model reports them verbatim instead of inventing a series. These
        # ARE the numbers; do not restate them from memory.
        if len(series) == 1:
            summary = f"{metric_id}={series[0]['value']} over {period_start.date()}..{period_end.date()}"
        else:
            shown = series[:_MAX_SERIES_IN_SUMMARY]
            body = "; ".join(f"{s['label']}={s['value']}" for s in shown)
            more = "" if len(series) <= _MAX_SERIES_IN_SUMMARY else f"; …(+{len(series) - _MAX_SERIES_IN_SUMMARY} more rows in evidence)"
            summary = (
                f"{metric_id} over {period_start.date()}..{period_end.date()} "
                f"({len(series)} rows) — use these exact values: {body}{more}"
            )
        prov = ArtifactProvenance(
            query_version=provenance.query_version,
            source_metadata={**(provenance.source_metadata or {}), "series": series},
        )
        # Working memory: record the established value so the model re-reads it
        # next turn instead of re-issuing this query (restates the summary it
        # already holds — not a new number, so rule 6's evidence chain is
        # untouched). In-process append; no I/O, no added latency.
        ctx.deps.scratchpad.note(summary)
        return ToolResult(
            success=True,
            artifact_ids=artifact_ids,
            summary=summary,
            provenance=prov,
            warnings=list(result.get("warnings") or []),
        )

    # A cache HIT above means the same fetch already ran this mission — but
    # every call to this function still reached this point and would write a
    # fresh, duplicate set of EvidenceArtifacts for identical rows (live
    # 2026-09-21: a 200-row product_title breakdown was called twice 18s
    # apart, doubling the evidence the model had to re-read next turn).
    # Cache the built ToolResult too, so a repeat call reuses the same
    # artifact_ids instead of writing them again.
    if not _QUERY_CACHE_ENABLED:
        return await _write_evidence()
    result_key = _cache_key("query_metrics_result", args)
    prior = ctx.deps.query_cache.peek(result_key)
    if prior is not None and prior.success:
        # The model already fetched this exact query this mission and is
        # re-issuing it verbatim (live 2026-09-22 MS3-0b46db4d98: a lookup
        # re-called an identical successful query_metrics ~10x and exhausted
        # its step budget; MS3-0bb3863a2e: 3 identical calls despite the nudge).
        # A returned success — even a nudge — still reads as "call succeeded" and
        # a stubborn small model calls again. So escalate: nudge once, then hard
        # ModelRetry to force final_result. The evidence from the first call is
        # already in the store/context, so this loses nothing.
        dup_key = f"dup:{result_key}"
        dups = ctx.deps.call_counts.get(dup_key, 0) + 1
        ctx.deps.call_counts[dup_key] = dups
        if dups >= 2:
            raise ModelRetry(
                "You have already fetched this exact query and have all its "
                "values above. Do NOT call query_metrics again — call "
                "final_result now with the values you already have."
            )
        return prior.model_copy(
            update={
                "summary": (
                    f"ALREADY FETCHED — {prior.summary}. You have these values; "
                    "write your final_response now. Do NOT call query_metrics "
                    "for this metric/period again — it returns the same rows."
                )
            }
        )
    return await ctx.deps.query_cache.get_or_fetch(result_key, _write_evidence)


def _resolve_drilldown_parent_id(
    parent: dict[str, Any], metric_id: str
) -> tuple[str | None, str | None]:
    """Pick the query id to drill into. A single-view parent → its ``query_id``.
    A composed multi-view parent → the sole part id if there is exactly one,
    else ``(None, reason)`` so the caller refuses instead of sending the
    composition id (which the server rejects). ``composed``/``part_query_ids``
    live either top-level or under ``provenance``."""
    prov = parent.get("provenance") or {}
    composed = bool(parent.get("composed") or prov.get("composed"))
    if not composed:
        return parent.get("query_id"), None
    part_ids = parent.get("part_query_ids") or prov.get("part_query_ids") or [
        p.get("query_id") for p in (parent.get("parts") or []) if p.get("query_id")
    ]
    part_ids = [pid for pid in part_ids if pid]
    if len(part_ids) == 1:
        return part_ids[0], None
    return None, (
        f"'{metric_id}' spans multiple Cube views, so it can't be drilled down as one "
        f"query. Pick a single-view metric for the '{metric_id}' concept and retry."
    )


async def drilldown(
    ctx: RunContext[SelericDeps],
    metric_id: str,
    dimension: str,
    period_start: datetime,
    period_end: datetime,
) -> ToolResult:
    """Breakdown of ``metric_id`` by ``dimension`` over a period.

    The live ``metrics_drilldown`` tool drills into a prior ``metrics_query``
    result by its ``parent_query_id`` — it has no metric/period-only form.
    This wrapper runs the parent query itself so the frozen tool signature
    (metric_id/dimension/period, no query-id bookkeeping) stays the agent's
    contract; that's orchestration of the live two-call API, not a new
    heuristic.
    """
    parent_args = build_metrics_query_args(
        measure=metric_id,
        start=period_start.date().isoformat(),
        end=period_end.date().isoformat(),
    )
    # Same args shape (and cache key) as an unfiltered query_metrics() call —
    # a prior plain total for this metric/period is reused here instead of
    # re-issuing an identical parent query against Cube.
    parent = await _cached_metrics_query(ctx, parent_args)
    if parent.get("error") or not parent.get("query_id"):
        return _fetch_failure(
            f"drilldown({metric_id}) parent query", parent.get("error") or "no query_id"
        )
    # A metric spanning multiple Cube views comes back composed: the server
    # rejects the composition id and only accepts a single part_query_id (see
    # server.py metrics_drilldown/insights_explain). Resolve to the one part, or
    # refuse clearly rather than sending the composition id and surfacing a raw
    # server rejection.
    parent_query_id, multi_view_reason = _resolve_drilldown_parent_id(parent, metric_id)
    if parent_query_id is None:
        return ToolResult(
            success=False,
            summary=multi_view_reason or f"drilldown({metric_id}) parent has no usable query id",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
        )
    drilldown_args = {"parent_query_id": parent_query_id, "target_dimensions": [dimension]}
    fetch_drilldown = lambda: ctx.deps.mcp_client.call(  # noqa: E731
        agent_id=_AGENT_ID, capability="seleric.metrics_drilldown", arguments=drilldown_args
    )
    try:
        if _QUERY_CACHE_ENABLED:
            result: dict[str, Any] = await ctx.deps.query_cache.get_or_fetch(
                _cache_key("seleric.metrics_drilldown", drilldown_args), fetch_drilldown
            )
        else:
            result = await fetch_drilldown()
    except Exception as exc:
        return _mcp_error_result(exc)
    rows = (result or {}).get("rows") or []
    if not rows:
        return ToolResult(
            success=False,
            summary=f"no drilldown rows for {metric_id} by {dimension}",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
        )
    provenance = ArtifactProvenance(source_metadata=result.get("provenance") or {})
    artifact_ids: list[str] = []
    for row in rows:
        value = row.get(metric_id)
        if value is None:
            continue
        evidence = EvidenceArtifact(
            metric_id=metric_id,
            dimensions={dimension: str(dimension_value(row, dimension))},
            grain="none",
            as_of=ctx.deps.as_of,
            period_start=period_start,
            period_end=period_end,
            value=float(value),
            source_query={"parent_query_id": parent["query_id"], "target_dimensions": [dimension]},
        )
        artifact = ctx.deps.artifact_store.put(
            Artifact(
                workspace_id=ctx.deps.principal.workspace_id,
                artifact_type="evidence",
                payload=evidence.model_dump(mode="json"),
                classification="factual",
                evidence_ids=[f"raw:{metric_id}:{dimension}:{dimension_value(row, dimension)}"],
                provenance=provenance,
                mission_id=ctx.deps.mission_id,
            )
        )
        artifact_ids.append(artifact.id)
    if not artifact_ids:
        return ToolResult(
            success=False,
            summary=f"drilldown rows for {metric_id} by {dimension} had no usable values",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
        )
    return ToolResult(
        success=True,
        artifact_ids=artifact_ids,
        summary=f"{metric_id} by {dimension}: {len(artifact_ids)} row(s)",
        provenance=provenance,
    )
