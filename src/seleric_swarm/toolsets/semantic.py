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

import asyncio
import calendar
import difflib
import json
import re
from collections.abc import Awaitable
from datetime import date as _date
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import ModelRetry, RunContext

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.limits import withdraw_tool
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.services.elapsed import (
    ELAPSED_KEY,
    completed_hours,
    covers_in_progress_day,
    in_progress_day,
)
from seleric_swarm.services.mcp_query import (
    _BRAND_DIM_KEYS,
    DEFAULT_BRAND_ID,
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


# A dimension value is one literal, or a list of literals meaning "any of these"
# (live: WhatsApp orders are utm_medium in {whatsapp, wa} — one value per filter
# forced two queries and a hand-summed answer).
DimensionValue = str | list[str]

FilterOperator = Literal[
    "equals", "notEquals", "contains", "notContains", "startsWith", "endsWith",
    "gt", "gte", "lt", "lte", "set", "notSet",
]


class MetricFilter(BaseModel):
    """One structured filter. ``dimension`` is a catalogue dimension id — or a metric id of the same view,
    which filters the aggregated value (comparison operators only: keep the entities of a breakdown whose
    metric is above / below a number). ``set`` / ``notSet`` take no values."""

    dimension: str
    operator: FilterOperator = "equals"
    values: list[str] = Field(default_factory=list)


def _sanitize_dimensions(
    dimensions: dict[str, DimensionValue] | None,
) -> dict[str, DimensionValue]:
    cleaned: dict[str, DimensionValue] = {}
    for key, raw in (dimensions or {}).items():
        val: str | list[str] | None = raw
        if isinstance(val, (list, tuple)):
            kept = [
                str(v).strip()
                for v in val
                if v is not None and str(v).strip() and not _is_placeholder_dimension_value(str(v).strip())
            ]
            if len(kept) > 1:
                cleaned[str(key)] = list(dict.fromkeys(kept))
                continue
            val = kept[0] if kept else None
        if val is None:
            cleaned[str(key)] = ""
            continue
        text = str(val).strip()
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
_MAX_DEFINITION_LOOKUPS = 8


def _definition_cache_key(metric_id: str) -> str:
    """Per-mission cache slot for one metric's definition, shared by the
    singular and batch definition tools so a metric fetched by either is never
    re-fetched by the other, and repeats cost no MCP call or lookup budget.
    RepeatCallGuard only dedups byte-identical args, so it misses the
    singular↔batch overlap (live L3/L10: definitions re-fetched after querying).
    Namespaced off the ``metrics_query`` keys that share ``query_cache``."""
    return f"metric_definition:{metric_id}"


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
    import asyncio

    key = _cache_key("seleric.metrics_query", arguments)

    def call() -> Awaitable[dict[str, Any]]:
        return call_metrics_query(ctx.deps.mcp_client, agent_id=_AGENT_ID, arguments=arguments)

    def fetch() -> Awaitable[dict[str, Any]]:
        if not _QUERY_CACHE_ENABLED:
            return call()
        # call_metrics_query returns failures as {"error": ...} values; caching
        # one would replay a transient MCP fault for the rest of the mission,
        # even though _fetch_failure tells the model it is retryable.
        return ctx.deps.query_cache.get_or_fetch(
            key, call, cacheable=lambda r: not r.get("error")
        )

    if _QUERY_CACHE_ENABLED and ctx.deps.query_cache.peek(key) is not None:
        # Already fetched this mission -- a cache hit costs no real Cube
        # query, so it must not consume max_cube_queries (ExecutionLimits,
        # CONTRACTS.md) either.
        return await fetch()
    verdict = ctx.deps.budget.consume("cube_queries")
    if not verdict.ok:
        # Withdraw the fetch tools, don't ModelRetry: an ignored retry counts
        # toward the per-tool retry limit and fails the whole mission.
        withdraw_tool(ctx.deps, "query_metrics", "drilldown")
        return {
            "error": (
                f"Cube query budget exhausted for this mission ({verdict.reason}). "
                "Do not fetch any more data -- write your final_response now using "
                "the evidence you already have, and say plainly if that isn't enough."
            )
        }
    mcp_timeout = float(getattr(ctx.deps.limits, "mcp_call_timeout_s", 15.0))
    try:
        result = await asyncio.wait_for(fetch(), timeout=mcp_timeout)
    except TimeoutError:
        return {
            "error": f"MCP call timed out after {mcp_timeout}s",
            "rows": [],
            "provenance": {},
        }
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
    ``"<Type>: <msg>"``, so an unconfigured MCP is recognised by its type name.

    Deterministic failures (a query-shape the warehouse can't run, or invalid
    arguments) are marked NON-retryable so the repeat guard does not re-execute a
    call that can never succeed (live: a weekly-grain ROAS 500'd on a Cube memory
    limit and was retried 3x). The model must change the call, not replay it."""
    text = str(error)
    if text.startswith("NotImplementedError"):
        return _mcp_error_result(NotImplementedError(text))
    low = text.lower()
    # memory/complexity limit: the query shape is too large — a coarser grain or
    # shorter period is the fix, never an unchanged retry.
    if "memory limit" in low or "memory_limit" in low or "too many" in low or "max_memory" in low:
        return ToolResult(
            success=False,
            summary=(
                f"{what} failed — the warehouse hit a size/memory limit on this query shape. "
                "Do NOT retry unchanged: use a coarser grain (e.g. month instead of week) or a "
                "shorter period, or fetch the period total (grain=none)."
            ),
            error_code="UNSUPPORTED_QUERY",
            retryable=False,
        )
    # invalid arguments (grain/enum/validation): deterministic — correct the call.
    if "validationerror" in low or "literal_error" in low or "input should be" in low or "granularity" in low:
        return ToolResult(
            success=False,
            summary=(
                f"{what} failed — invalid argument(s): {text}. Correct the call to a supported "
                "value; do NOT replay the same arguments."
            ),
            error_code="UNSUPPORTED_QUERY",
            retryable=False,
        )
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

# After this many search ROUNDS in one mission, search_semantics stops returning
# a fresh-looking result and forces the model to commit — a mechanical breaker
# for the paraphrase-search loop (SEARCH-01) that exact-arg caching can't catch.
# A round is one model step: a paraphrase loop searches one step after another,
# while parallel searches in ONE step are distinct concepts of a broad question.
# Counting calls instead withdrew the tool mid-fan-out (live 2026-10-06
# MS3-6a09be7996: 11 parallel searches, one per metric the question named; the
# last 6 were refused and never resolved).
_MAX_SEARCHES = 5
# Backstop on the raw call count, so a huge single fan-out is still bounded.
_MAX_SEARCH_CALLS = 24

# After this many *empty* search rounds (no catalogue match), the concept is
# almost certainly not modelled — stop before the full _MAX_SEARCHES budget so
# the model concludes "not available" instead of paraphrasing into the wall
# (live L12: 4 empty searches for un-modelled inventory metrics). One empty is
# tolerated: a rephrase can still land the right term. Counted per round for
# the same reason as _MAX_SEARCHES: two unmodelled concepts in one parallel
# fan-out are not a rephrase.
_MAX_EMPTY_SEARCHES = 2


def _rounds(ctx: RunContext[SelericDeps], key: str) -> int:
    """Record this call's model step under ``key``; return how many distinct
    steps have used it. A context without ``run_step`` counts every call as its
    own round."""
    steps: list[Any] = ctx.deps.call_counts.setdefault(key, [])
    step = getattr(ctx, "run_step", None)
    marker = step if step is not None else f"call-{len(steps)}"
    if marker not in steps:
        steps.append(marker)
    return len(steps)

# Key for storing best metric ids from successful searches in call_counts
_SEARCH_RESULTS_KEY = "search_semantics_results"



def _empty_search_verdict(ctx: RunContext[SelericDeps]) -> ToolResult | None:
    """Withdraw search_semantics once repeated searches keep coming back empty,
    so an un-modelled concept ends in a decisive "not available" rather than
    burning the paraphrase budget. Returns ``None`` while empties are tolerated."""
    empties = _rounds(ctx, "search_semantics_empty_steps")
    if empties < _MAX_EMPTY_SEARCHES:
        return None
    withdraw_tool(ctx.deps, "search_semantics")
    return ToolResult(
        success=True,
        summary=(
            "No catalogue metric matches this concept after repeated searches — it is "
            "not modelled. Catalogue search is now disabled for this mission: do not "
            "search again; tell the user this data is not available."
        ),
        provenance=ArtifactProvenance(source_metadata={"matches": []}),
    )

def _fmt_value(value: float) -> str:
    """Compact number for a summary line: integers without ".0", amounts to 2 decimals, small ratios
    to 4 significant digits (a CTR of 0.020819 reads 0.02082, not 0). The model copies summary numbers
    verbatim, so raw Cube floats ("261300.63999999932") reached answers (golden 2026-10-08); evidence keeps
    the precise value and the provenance check allows print-precision rounding."""
    if value.is_integer():
        return str(int(value))
    if abs(value) >= 1:
        return f"{value:.2f}"
    return f"{value:.4g}"


# Cap the per-row values echoed into the tool summary. Top-N already limits
# rows; this bounds a large ungrouped breakdown. Every row still lands in
# evidence + source_metadata["series"]; the summary just shows the first N.
_MAX_SERIES_IN_SUMMARY = 40


def _series_stats(ctx: RunContext[SelericDeps], metric_id: str, values: list[float]) -> str:
    """Row statistics computed here, so the model quotes a total instead of adding rows.

    Live 2026-10-04 (MS3-34e7eb26aa): four of seven drafts stated a wrong sum of
    27 daily rows, each one rejected by the arithmetic audit. Whether the rows may
    be summed at all is the catalogue's ``aggregation`` — never guessed from the
    id or the unit: an additive metric gets a total and per-row average, a ratio
    is told it cannot be summed, and a metric the catalogue does not type gets
    only its range.
    """
    if len(values) < 2:
        return ""
    catalogue = getattr(ctx.deps, "catalogue", None)
    aggregation = None
    if catalogue is not None and hasattr(catalogue, "aggregation_for"):
        for candidate in dict.fromkeys((metric_id, ctx.deps.canonical_metric_id(metric_id))):
            aggregation = catalogue.aggregation_for(candidate)
            if aggregation:
                break
    span = f"min={_fmt_value(min(values))}, max={_fmt_value(max(values))}"
    if aggregation == "additive":
        total = sum(values)
        return (
            f" Computed over all {len(values)} rows: total={_fmt_value(total)}, "
            f"average per row={_fmt_value(total / len(values))}, {span}. "
            "Quote these figures; do not re-add the rows."
        )
    if aggregation == "ratio":
        return (
            f" Range over all {len(values)} rows: {span}. {metric_id} is a ratio: its rows "
            "cannot be summed or averaged into a period figure — query it with grain='none' "
            "for that."
        )
    return f" Range over all {len(values)} rows: {span}."


# What makes two evidence rows the same fact. ``as_of``/``fetched_at`` are left
# out: a retry fetching the identical row a minute later is not new evidence.
_EVIDENCE_IDENTITY = (
    "metric_id", "dimensions", "grain", "period_start", "period_end", "value", "unit", "source_query",
)


def _evidence_identity(payload: dict[str, Any]) -> str:
    return json.dumps({k: payload.get(k) for k in _EVIDENCE_IDENTITY}, sort_keys=True, default=str)


def _evidence_index(ctx: RunContext[SelericDeps]) -> dict[str, str]:
    """identity -> artifact id for the evidence this mission already holds."""
    index: dict[str, str] = {}
    for artifact in ctx.deps.artifact_store.list_for_mission(ctx.deps.mission_id):
        if artifact.artifact_type == "evidence" and isinstance(artifact.payload, dict):
            index.setdefault(_evidence_identity(artifact.payload), artifact.id)
    return index


def _put_evidence(
    ctx: RunContext[SelericDeps],
    evidence: EvidenceArtifact,
    *,
    index: dict[str, str],
    raw_id: str,
    provenance: ArtifactProvenance,
) -> str:
    """Write one EvidenceArtifact, or reuse the identical one the mission holds.

    The per-run query cache only dedupes within one set of deps. A recovery
    retry of the same mission gets fresh deps but the same artifact store, so
    it re-wrote every row (live MS3-34e7eb26aa: 54 evidence rows for 27 days,
    each day twice) — anything summing the mission's evidence double-counted.
    """
    payload = evidence.model_dump(mode="json")
    identity = _evidence_identity(payload)
    if (existing := index.get(identity)) is not None:
        return existing
    artifact = ctx.deps.artifact_store.put(
        Artifact(
            workspace_id=ctx.deps.principal.workspace_id,
            artifact_type="evidence",
            payload=payload,
            classification="factual",
            evidence_ids=[raw_id],
            provenance=provenance,
            mission_id=ctx.deps.mission_id,
        )
    )
    index[identity] = artifact.id
    return artifact.id


# Catalogue descriptions open with a one-sentence "what it is + when to use it";
# echo only that so the model can tell near-identical ids apart (e.g. Meta net
# sales on the event-date basis vs order-date) without a definitions round-trip.
_SUMMARY_MAX_CHARS = 280


def _summary(description: str) -> str:
    text = " ".join((description or "").split())
    # First sentence: a period followed by a space and an upper-case letter,
    # so decimals ("52.7") and abbreviations inside the sentence don't cut it.
    m = re.search(r"\.\s+(?=[A-Z])", text)
    first = text[: m.start() + 1] if m else text
    return first if len(first) <= _SUMMARY_MAX_CHARS else first[: _SUMMARY_MAX_CHARS - 1].rstrip() + "…"


def _slim_match(match: dict[str, Any]) -> dict[str, Any]:
    slim = {k: match[k] for k in _SHORTLIST_FIELDS if k in match}
    summary = _summary(str(match.get("description") or ""))
    if summary:
        slim["summary"] = summary
    return slim


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
    rounds = _rounds(ctx, "search_semantics_steps")
    # Hard budget: past the cap, search_semantics is disabled for the mission —
    # a ModelRetry redirect, not an advisory string the model can ignore (live
    # 2026-09-22 MS3-53296c1a5e: the soft "STOP SEARCHING" summary was ignored
    # 11 times until the step budget tripped). The model already has candidates
    # from earlier searches; force it to execute or report no compatible metric.
    if rounds > _MAX_SEARCHES or count > _MAX_SEARCH_CALLS:
        withdraw_tool(ctx.deps, "search_semantics")
        # Include best metric ids from earlier successful searches
        prior_results: list[dict[str, Any]] = ctx.deps.call_counts.get(_SEARCH_RESULTS_KEY, [])
        prior_metric_ids = [r.get("id") for r in prior_results if r.get("id")]
        prior_hint = ""
        if prior_metric_ids:
            prior_hint = (
                f" Your earlier searches found these metrics: {', '.join(prior_metric_ids[:5])}. "
                "Pass one of these to query_metrics."
            )
        return ToolResult(
            success=False,
            summary=(
                "SEMANTIC_RESOLUTION_LOOP: catalogue search is disabled for this "
                "mission (budget exhausted). Call query_metrics with the best metric "
                "id from your earlier search results — for a product/SKU question use "
                "a product_* metric (e.g. product_net_revenue, product_return_revenue, "
                "returned_units)."
                f"{prior_hint} If no metric supports the breakdown you need, call "
                "final_result stating that plainly."
            ),
            error_code="SEMANTIC_RESOLUTION_LOOP",
            retryable=False,
        )
    try:
        result = await ctx.deps.mcp_client.call(
            agent_id=_AGENT_ID,
            capability="seleric.catalogue_search_metrics",
            arguments={"query": query},
        )
        hoisted = _hoist_exact(query, list((result or {}).get("matches") or []), ctx.deps.catalogue)
        matches = [_slim_match(m) for m in hoisted][:_SEARCH_SHORTLIST]
        if not matches and (verdict := _empty_search_verdict(ctx)) is not None:
            return verdict
        # Store successful search results for SEMANTIC_RESOLUTION_LOOP error
        if matches:
            existing: list[dict[str, Any]] = list(ctx.deps.call_counts.get(_SEARCH_RESULTS_KEY, []))
            # Keep only unique metric ids, most recent first
            seen = set()
            for m in matches:
                mid = m.get("id")
                if mid and mid not in seen:
                    seen.add(mid)
                    existing.insert(0, {"id": mid, "display_name": m.get("display_name", "")})
            ctx.deps.call_counts[_SEARCH_RESULTS_KEY] = existing[:10]
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
        if not matches and (verdict := _empty_search_verdict(ctx)) is not None:
            return verdict
        # Store successful search results for SEMANTIC_RESOLUTION_LOOP error
        if matches:
            existing = list(ctx.deps.call_counts.get(_SEARCH_RESULTS_KEY, []))
            seen = set()
            for m in matches:
                mid = m.get("id")
                if mid and mid not in seen:
                    seen.add(mid)
                    existing.insert(0, {"id": mid, "display_name": m.get("display_name", "")})
            ctx.deps.call_counts[_SEARCH_RESULTS_KEY] = existing[:10]
        warnings = [] if matches else [f"no catalogue match for '{query}'"]
        if any(m.get("stale") for m in matches):
            warnings.append("catalogue index may be stale; rerun scripts/sync_catalogue_to_qdrant.py")
        return ToolResult(
            success=True,
            summary=f"{len(matches)} metric(s) matched '{query}' (local index)",
            warnings=warnings,
            provenance=ArtifactProvenance(source_metadata={"matches": matches}),
        )


_DATE_AXIS = re.compile(r"Date axis:\s*([^;.]+)(?:;\s*grain:\s*([^.;]+))?", re.IGNORECASE)


async def list_metrics(ctx: RunContext[SelericDeps], domain: str | None = None) -> ToolResult:
    """Every metric you can query, grouped by domain (view), with its date axis,
    row grain and time buckets. Use it for "what can you query / list your
    metrics / what data do you have" — one call, no search. ``domain``
    narrows to one domain (e.g. "paid_media"). Listing only: no values."""
    # Live 2026-10-05 MS3-29049b3e92: with no listing tool the agent searched
    # twice, then shipped "I don't have a single " as a completed answer.
    metrics = list(ctx.deps.catalogue.metrics)
    if domain:
        wanted = domain.strip().lower()
        metrics = [m for m in metrics if wanted in (m.view or "").lower() or wanted in str((m.raw or {}).get("category") or "").lower()]
    if not metrics:
        domains = sorted({m.view for m in ctx.deps.catalogue.metrics if m.view})
        return ToolResult(
            success=False,
            summary=f"no metrics in domain '{domain}'. Domains: {', '.join(domains) or 'none loaded'}",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
        )
    by_view: dict[str, list[str]] = {}
    for m in sorted(metrics, key=lambda m: (m.view, m.id)):
        raw = m.raw or {}
        axis = _DATE_AXIS.search(str(raw.get("description") or ""))
        buckets = ["day", "week", "month", "quarter", "year", *list(raw.get("extra_granularities") or [])]
        parts = [str(raw.get("display_name") or m.label or m.id)]
        if raw.get("aggregation"):
            parts.append(str(raw["aggregation"]))
        if axis:
            parts.append(f"date: {axis.group(1).strip()}" + (f", row grain: {axis.group(2).strip()}" if axis.group(2) else ""))
        parts.append("buckets: " + "/".join(dict.fromkeys(buckets)))
        by_view.setdefault(m.view or "other", []).append(f"{m.id} ({'; '.join(parts)})")
    body = "\n".join(f"[{view}] " + " | ".join(items) for view, items in by_view.items())
    return ToolResult(
        success=True,
        summary=f"{len(metrics)} metrics in {len(by_view)} domain(s). Describe them in plain names, not ids:\n{body}",
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


# Evidence written per semantic_sql call: one artifact per numeric cell, so a
# broad result can't flood the mission store. Past the cap the rows still reach
# the model in the summary, flagged as uncited.
_SQL_MAX_EVIDENCE = 400


def _sql_cell_day(value: str) -> str | None:
    """The calendar day a label cell holds (an ISO date/timestamp), else None.
    All-digit strings are ids, not compact ISO dates."""
    if value.isdigit():
        return None
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        return None


def _sql_cell_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


async def semantic_sql(
    ctx: RunContext[SelericDeps],
    sql: str,
    period_start: str,
    period_end: str,
    brand_id: str | None = None,
    max_rows: int | None = None,
) -> ToolResult:
    """Ad-hoc analysis via Cube Core's Semantic SQL (Postgres protocol) — ONLY for
    derivations ``query_metrics`` / ``drilldown`` cannot express: CTEs, window
    functions (running totals, moving averages, rank within a group), ratios of
    measures from two views. Prefer ``query_metrics`` for any certified metric it
    can return directly.

    Write Postgres SQL over the governed Cube views (the views the catalogue's
    metrics name); aggregate measures with ``MEASURE(<column>)`` and GROUP BY the
    dimension columns. Filter the dates in SQL yourself and pass the same window
    as ``period_start`` / ``period_end`` (YYYY-MM-DD, inclusive) — that window is
    recorded on the evidence. The brand is scoped by Cube (``brand_id``, default
    the workspace brand), not by your WHERE clause; a cube that has no brand
    cannot be queried here. Every numeric cell becomes a citable evidence
    artifact (cite the returned ids in evidence_ids). Single SELECT/WITH
    statement, read-only, rows capped, 30s timeout. Tables and columns are checked
    against the views before anything runs: an unknown name comes back with that
    view's columns."""
    brand = str(brand_id or DEFAULT_BRAND_ID)
    try:
        start = datetime.fromisoformat(period_start[:10]).replace(tzinfo=ctx.deps.as_of.tzinfo)
        end = datetime.fromisoformat(period_end[:10]).replace(tzinfo=ctx.deps.as_of.tzinfo)
    except (TypeError, ValueError):
        return ToolResult(
            success=False,
            summary="semantic_sql needs period_start and period_end as YYYY-MM-DD (the window the SQL filters)",
            error_code="SEMANTIC_SQL_ERROR",
            retryable=False,
        )
    try:
        result = await ctx.deps.mcp_client.call(
            agent_id=_AGENT_ID,
            capability="seleric.semantic_sql",
            arguments={"sql": sql, "max_rows": max_rows, "brand_id": brand, "session_key": ctx.deps.mission_id},
        )
    except Exception as exc:
        return _mcp_error_result(exc)
    result = dict(result or {})
    if result.get("error"):
        # A schema rejection names the valid columns: correct the SQL once with
        # those names. Anything else (rate limit, Cube failure) is not retryable.
        correctable = bool(result.get("retryable"))
        return ToolResult(
            success=False,
            summary=f"semantic_sql failed: {result['error']}"
            + (" — fix the SQL with the names listed and call once more." if correctable else ""),
            error_code="SEMANTIC_SQL_ERROR",
            retryable=correctable,
            provenance=ArtifactProvenance(source_metadata=result),
        )
    rows = [r for r in (result.get("data") or []) if isinstance(r, dict)]
    meta = {k: v for k, v in result.items() if k != "data"}
    # The brand scope is a Cube-side filter: record it like query_metrics' filters
    # so scope coverage sees the answer as brand-filtered.
    meta["filters_applied"] = [{"dimension": "brand_id", "operator": "equals", "values": [brand]}]
    meta["sql"] = sql[:2000]
    provenance = ArtifactProvenance(source_metadata=meta)
    known_evidence = _evidence_index(ctx)
    source_query = {"semantic_sql": sql[:2000], "brand_id": brand, "query_sha": result.get("query_sha")}
    artifact_ids: list[str] = []
    lines: list[str] = []
    uncited = 0
    for row in rows:
        labels = {str(k): str(v) for k, v in row.items() if _sql_cell_number(v) is None and v is not None}
        dims = {k: (_sql_cell_day(v) or v) for k, v in labels.items()}
        day = next((d for v in labels.values() if (d := _sql_cell_day(v))), None)
        row_start = datetime.fromisoformat(day).replace(tzinfo=start.tzinfo) if day else start
        row_end = row_start if day else end
        cells = []
        for column, value in row.items():
            number = _sql_cell_number(value)
            if number is None:
                continue
            cells.append(f"{column}={number:g}")
            if len(artifact_ids) >= _SQL_MAX_EVIDENCE:
                uncited += 1
                continue
            evidence = EvidenceArtifact(
                metric_id=str(column),
                dimensions=dims,
                grain="day" if day else "none",
                as_of=ctx.deps.as_of,
                period_start=row_start,
                period_end=row_end,
                value=number,
                source_query=source_query,
            )
            artifact_ids.append(
                _put_evidence(
                    ctx,
                    evidence,
                    index=known_evidence,
                    raw_id=f"raw:semantic_sql:{result.get('query_sha', '')}:{column}",
                    provenance=provenance,
                )
            )
        label = ", ".join(f"{k}={v}" for k, v in dims.items())
        lines.append(f"{label} | {'; '.join(cells)}" if label else "; ".join(cells))
    if not artifact_ids:
        return ToolResult(
            success=False,
            summary=(
                f"semantic_sql returned {len(rows)} row(s) with no numeric value "
                f"over {start.date()}..{end.date()} (brand {brand})"
            ),
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
            provenance=provenance,
        )
    shown = lines[:_MAX_SERIES_IN_SUMMARY]
    more = "" if len(lines) <= len(shown) else f"; …(+{len(lines) - len(shown)} more rows in evidence)"
    summary = (
        f"semantic_sql over {start.date()}..{end.date()} (brand {brand}, {len(rows)} rows"
        + (", rows capped" if result.get("limited") else "")
        + f") — use these exact values: {' || '.join(shown)}{more}."
    )
    warnings = [f"{uncited} value(s) past the evidence cap are not citable"] if uncited else []
    return ToolResult(
        success=True,
        artifact_ids=artifact_ids,
        summary=summary,
        warnings=warnings,
        provenance=provenance,
    )


async def resolve_concept(
    ctx: RunContext[SelericDeps],
    concept: str,
    axes: dict[str, str] | None = None,
) -> ToolResult:
    """Deterministically map a business concept to exactly ONE catalogue metric id.
    Prefer this over ``search_semantics`` for a specific metric: pass the concept in
    the user's own words (natural language, not a metric id) plus any axes their
    phrasing implies. The catalogue owns the axis vocabulary and defaults, so you
    never hard-code axis values; unspecified axes take their disclosed default.

    Returns the resolved ``metric_id`` (feed straight to ``query_metrics``), the
    ``axes`` applied and which were defaulted, and any ``filter`` the concept binds
    (pass it through to ``query_metrics`` unchanged). A draft target is flagged; an
    unsupported axis combination returns a reason plus nearest metrics (never a wrong
    sibling); an unmodelled concept returns suggestions. On unsupported/unknown, fall
    back to ``search_semantics``. Resolution only — the id is validated by Cube on
    query.

    Pass only the measure phrase: breakdowns ("by campaign"), periods and filters
    belong in ``query_metrics``. Live 2026-10-07 the measure plus a breakdown
    resolved to a different metric than the measure alone."""
    # The axes the user's OWN words set (read by the gateway from the whole question) win over what the
    # extracted term carries: "net profit on the P&L" keeps date=finance even when only "net profit" is
    # passed here. Axis names / values are the catalogue's; nothing is listed in this code.
    stated = dict(getattr(ctx.deps.required_scope, "question_axes", ()) or ())
    merged_axes = {**(axes or {}), **stated}
    try:
        result = await ctx.deps.mcp_client.call(
            agent_id=_AGENT_ID,
            capability="seleric.catalogue_resolve_concept",
            arguments={"text": concept, "axes": merged_axes},
        )
    except Exception as exc:
        return _mcp_error_result(exc)
    result = dict(result or {})
    kind = result.get("kind")
    if kind == "resolved_concept":
        mid = result.get("metric_id")
        warnings: list[str] = []
        if result.get("defaults_applied"):
            warnings.append("defaults applied: " + ", ".join(result["defaults_applied"]))
        if result.get("draft"):
            warnings.append(f"'{mid}' is a draft metric" + (f": {result['note']}" if result.get("note") else ""))
        if result.get("used_fallback") and result.get("note"):
            warnings.append(str(result["note"]))
        if result.get("disambiguation"):
            warnings.append(str(result["disambiguation"]))
        axes_str = ", ".join(f"{k}={v}" for k, v in (result.get("axes") or {}).items())
        filter_bound = result.get("filter")
        filter_str = ""
        if filter_bound and isinstance(filter_bound, dict):
            filter_str = f" — bound filter: {filter_bound}. Pass this in dimensions to query_metrics!"
            if mid:
                ctx.deps.query_cache.set(f"concept_filter:{mid}", filter_bound)
        return ToolResult(
            success=True,
            summary=f"{concept} -> {mid}" + (f" ({axes_str})" if axes_str else "") + filter_str,
            warnings=warnings,
            provenance=ArtifactProvenance(source_metadata=result),
        )
    if kind == "unsupported_concept":
        nearest = ", ".join(result.get("nearest_metrics") or [])
        return ToolResult(
            success=False,
            summary=f"'{concept}' unsupported at those axes: {result.get('reason', '')}"
            + (f" — nearest: {nearest}" if nearest else "") + ". Try find_metrics with the user's other words.",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
            provenance=ArtifactProvenance(source_metadata=result),
        )
    sugg = ", ".join(result.get("suggestions") or [])
    return ToolResult(
        success=False,
        summary=f"no concept matched '{concept}'"
        + (f" — did you mean: {sugg}?" if sugg else "") + "",
        error_code="INSUFFICIENT_EVIDENCE",
        retryable=False,
        provenance=ArtifactProvenance(source_metadata=result),
    )


async def find_metrics(
    ctx: RunContext[SelericDeps],
    phrases: list[str] | None = None,
    axes: dict[str, str] | None = None,
    domain: str | None = None,
) -> ToolResult:
    """Find the catalogue metric id for every measure the question names — one call.

    Pass each measure in the user's own words (``phrases=["net sales", "ad spend",
    "roas"]``), measure words only: breakdowns, periods and filters belong in
    ``query_metrics``. Each phrase goes to the catalogue's deterministic concept
    resolver (axes the user's words imply may be passed in ``axes``); a phrase it
    cannot place falls back to the glossary-backed search, which returns ranked
    candidates. A resolved concept may bind a filter: pass it to ``query_metrics``
    unchanged. With no phrases, lists every metric you can query (optionally one
    ``domain``) — for "what data do you have". Resolution only: no values."""
    wanted = [p.strip() for p in (phrases or []) if p and p.strip()]
    if not wanted:
        return await list_metrics(ctx, domain)
    wanted = list(dict.fromkeys(wanted))
    resolved = await asyncio.gather(*(resolve_concept(ctx, p, axes) for p in wanted))
    misses = [p for p, r in zip(wanted, resolved, strict=True) if not r.success]
    searched = dict(zip(misses, await asyncio.gather(*(search_semantics(ctx, p) for p in misses)), strict=True))
    lines: list[str] = []
    warnings: list[str] = []
    found: dict[str, Any] = {}
    for phrase, result in zip(wanted, resolved, strict=True):
        if result.success:
            lines.append(f"- {result.summary}")
            warnings += [f"{phrase}: {w}" for w in result.warnings]
            found[phrase] = result.provenance.source_metadata
            continue
        fallback = searched[phrase]
        if fallback.success:
            lines.append(f"- '{phrase}' has no single concept; closest catalogue metrics: {fallback.summary}")
            found[phrase] = fallback.provenance.source_metadata
        else:
            lines.append(f"- '{phrase}': {result.summary} / {fallback.summary}")
    if not found:
        return ToolResult(
            success=False,
            summary="no catalogue metric found:\n" + "\n".join(lines),
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
        )
    return ToolResult(
        success=True,
        summary="\n".join(lines),
        warnings=warnings,
        provenance=ArtifactProvenance(source_metadata={"resolved": found}),
    )


def _pin_to_resolved_window(
    ctx: RunContext[SelericDeps],
    period_start: datetime | None,
    period_end: datetime | None,
) -> tuple[datetime, datetime, str] | None:
    """Hold the caller to a period the question named.

    ``services/time_range.py`` resolves a relative phrase to exact dates once,
    deterministically, precisely because the model drifts when it does the
    arithmetic itself — live, "last month" was fetched as 2026-08-01..08-30,
    dropping a real day of data and shifting every figure derived from it. That
    resolution reached the model only as a sentence in the prompt, which it is
    free to ignore, and it did.

    The correction is applied rather than refused. Refusing was tried first and
    is worse: the model re-issued the same dates until the mission timed out,
    then answered from a drilldown whose range it had inherited earlier — so the
    wrong window survived anyway, having burned the whole budget. A resolver
    that exists to be authoritative should not be arguable. The override is
    reported in ``warnings`` so it stays visible rather than silent.

    **A comparison question names two periods and both are pinnable.** Pinning
    only period A would make the second window unfetchable — the model would ask
    for "today", be corrected back to "last 3 days", and lose it. That is
    exactly the failure the comparison resolution exists to fix (live
    MS3-167d9f4838: the second window was dropped and never recovered), so a
    call matching *either* dated period of a comparison is accepted untouched.

    Fail-open when the question named no relative period, when the caller
    already asked for exactly that period, or when the comparison's second
    period is undated.
    """
    window = ctx.deps.resolved_window
    if window is None or period_start is None or period_end is None:
        return None
    if not window.start or not window.end:
        return None
    asked = (period_start.date().isoformat(), period_end.date().isoformat())
    pinnable = {(window.start, window.end)}
    if window.kind == "comparison" and window.start_b and window.end_b:
        pinnable.add((window.start_b, window.end_b))
    if asked in pinnable:
        return None
    # Drift is a miscomputed version of the named period, so it overlaps it ("last
    # month" fetched as 08-01..08-30). A window that shares no day with any named
    # period is a different period on purpose — the baseline of "compare to the last
    # 7 days", the previous week of a trend, the history a "why" needs. Pulling it
    # back made every comparison unfetchable: the model asked for 09-23..09-29 seven
    # times, got 09-30..10-06 each time, was told ALREADY FETCHED and answered "the
    # previous window could not be retrieved" (live 2026-10-07 MS3-d13b253ea0, and
    # golden Q5 "last 7 days versus the previous 7 days").
    if all(asked[1] < start or asked[0] > end for start, end in pinnable):
        return None
    token = (window.relative_token or "").replace("_", " ")
    tz = period_start.tzinfo
    return (
        datetime.fromisoformat(window.start).replace(tzinfo=tz),
        datetime.fromisoformat(window.end).replace(tzinfo=tz),
        (
            f"'{token}' is {window.start}..{window.end}; this call asked for "
            f"{asked[0]}..{asked[1]} and was corrected to the period the question names"
        ),
    )


async def _reject_unknown_metric(ctx: RunContext[SelericDeps], metric_id: str) -> ToolResult | None:
    """Gate an id against the warmed catalogue snapshot before any Cube call.
    Validation only — never rewrites the id to a guess (rule 1 / the
    no-alias-table warning in this module's docstring).

    * id present, or snapshot empty (fail-open) → ``None`` (proceed).
    * absent but close ids exist → ``ModelRetry`` listing them: the model can
      fix it in-context by picking a real id.
    * absent with nothing close → the concept is not modelled. Return a
      non-retryable failure so the model reports it / searches, instead of
      falling through to Cube — whose error is retryable, so the model looped
      guessing more non-existent ids (live L12: inventory_turnover_by_warehouse
      → inventory_on_hand_value_by_warehouse). A ModelRetry here would be wrong:
      there is no real id to re-pick, so an ignored retry just burns the budget.
    """
    catalogue = ctx.deps.catalogue
    if not catalogue.metrics or catalogue.has_metric(metric_id):
        return None
    candidates = catalogue.closest_metric_ids(metric_id)
    if candidates:
        raise ModelRetry(
            f"'{metric_id}' is not a catalogue metric id. Closest ids: "
            f"{', '.join(candidates)}. Pick the exact id from the [catalogue] "
            f"listing and retry."
        )
    # A business word passed as an id ("spend", "revenue") is not an unmodelled
    # concept: the catalogue's own concept resolver names its id. Without this the
    # model was told "not modelled — tell the user it is not available" and printed
    # spend and revenue as "No data available" for every campaign (live 2026-10-08,
    # golden Q9). Resolution only — the id comes from the catalogue, not this code.
    concept = await resolve_concept(ctx, metric_id.replace("_", " "))
    resolved = (concept.provenance.source_metadata or {}).get("metric_id") if concept.success and concept.provenance else None
    if resolved and resolved != metric_id and catalogue.has_metric(resolved):
        raise ModelRetry(
            f"'{metric_id}' is a business term, not a metric id: the catalogue resolves it to "
            f"'{resolved}' ({concept.summary}). Retry with metric_id='{resolved}'."
        )
    return ToolResult(
        success=False,
        summary=(
            f"'{metric_id}' is not a catalogue metric and no similar metric exists — "
            f"this concept is not modelled. Confirm with find_metrics if unsure; "
            f"otherwise tell the user it is not available. Do not guess another id."
        ),
        error_code="UNSUPPORTED_QUERY",
        retryable=False,
    )


def _period_from_time_values(value: DimensionValue) -> tuple[_date, _date] | None:
    """A time-dimension FILTER value read as the period it names (``"2026-10-01"`` or a list of dates →
    first..last). None when a value is not an ISO date."""
    try:
        days = sorted(_date.fromisoformat(str(v)[:10]) for v in (value if isinstance(value, list) else [value]))
    except ValueError:
        return None
    return (days[0], days[-1]) if days else None


def _fit_to_metric(
    catalogue: Any, metric_id: str, dimensions: dict[str, DimensionValue], filters: list[MetricFilter]
) -> tuple[
    dict[str, DimensionValue], list[MetricFilter], dict[str, str], list[str], tuple[_date, _date] | None, list[str]
]:
    """Map a request onto *metric_id*'s own slicing surface, catalogue-driven:

    * a time dimension of another view is this metric's own time axis — as a breakdown it is the grain
      (folded later), as a filter it names the period;
    * a dimension the metric's view lacks is answered by its conformed sibling (catalogue ``family``: e.g.
      platform → finance_channel on the P&L, ad_platform → acquisition_platform on customers) when the
      sibling's declared values hold the requested ones.

    Returns (dimensions, filters, renames {asked: used}, keys still unsupported, period named by a time
    filter). Nothing is guessed: a key with no catalogue mapping is reported, not dropped. A key whose family
    IS on this view but cannot hold the value (a traffic platform that is no P&L channel) is reported as
    ``"<key>=<values>"`` — a value gap, which no other grain of the same measure fixes."""
    supported = set(catalogue.supported_dimensions_for(metric_id))
    renames: dict[str, str] = {}
    missing: list[str] = []
    value_gaps: list[str] = []
    period: tuple[_date, _date] | None = None

    def place(key: str, values: list[str] | None) -> str | None:
        if not supported or key in supported or key == metric_id or catalogue.has_metric(key):
            return key
        sib = catalogue.conformed_sibling(key, supported, values)
        if sib is not None:
            renames[key] = sib
            return sib
        if values and catalogue.conformed_sibling(key, supported) is not None:
            value_gaps.append(f"{key}={','.join(values)}")
        return None

    out: dict[str, DimensionValue] = {}
    for key, value in dimensions.items():
        if catalogue.is_time_dimension(key):
            if not value:
                out[key] = value  # a date breakdown is the grain on any axis (folded in query_metrics)
            elif (named := _period_from_time_values(value)) is not None:
                period = named  # a date filter on any time axis is the period this metric reads
            else:
                missing.append(key)
            continue
        values = None if not value else (list(value) if isinstance(value, list) else [value])
        used = place(key, values)
        if used is None:
            missing.append(key)
        else:
            out[used] = value
    fitted: list[MetricFilter] = []
    for f in filters:
        used = place(f.dimension, f.values if f.operator in ("equals", "notEquals") else None)
        if used is None:
            missing.append(f.dimension)
        else:
            fitted.append(f.model_copy(update={"dimension": used}))
    return out, fitted, renames, list(dict.fromkeys(missing)), period, value_gaps


def _fold_equals_filters(
    ctx: RunContext[SelericDeps], dimensions: dict[str, DimensionValue], filters: list[MetricFilter]
) -> list[MetricFilter]:
    """An equals filter on a dimension is a dimension value (pooled, never an entity list): fold it into
    *dimensions* so evidence and the scope-coverage check see it exactly as a ``dimensions`` filter. The
    other operators (and metric-value filters) stay structured and travel in the query."""
    kept: list[MetricFilter] = []
    for f in filters:
        if f.operator == "equals" and f.values and not ctx.deps.catalogue.has_metric(f.dimension) \
                and not dimensions.get(f.dimension):
            dimensions[f.dimension] = f.values[0] if len(f.values) == 1 else list(f.values)
        else:
            kept.append(f)
    return kept


def _conform_dimensions(
    ctx: RunContext[SelericDeps],
    metric_id: str,
    dimensions: dict[str, DimensionValue],
    filters: list[MetricFilter] | None = None,
) -> tuple[str, dict[str, DimensionValue], list[MetricFilter], dict[str, str], list[str], tuple[_date, _date] | None]:
    """Fit a request to the catalogue instead of refusing it (live 2026-10-07: "Meta campaigns ranked by
    spend, revenue, ROAS, CAC, CTR, … LPVs, purchases" sent the Meta scope as ad_platform to orders,
    net_sales and page_views and as finance_channel to ad_spend / ctr / clicks; seven ModelRetry bounces in
    one step and the answer shipped with net ROAS only).

    1. The metric itself, with conformed siblings for dimensions its view lacks (``_fit_to_metric``).
    2. Else its catalogue grain twin (the concepts' ``scope`` axis: the same measure at the grain that
       carries the slice — page_views → session_page_views, cac → channel_cac, orders → product_orders) —
       answered directly and named in the summary. The twin is declared by the catalogue, never guessed.
    3. Else, when other metrics carry the dimension, ModelRetry naming them (a different concept: the model
       picks). When nothing carries it, Cube decides (a real capability gap, not a bad pick).

    Returns (metric id to query, dimensions, filters, renamed dimensions {asked: used}, notes for the
    summary, period named by a time filter). Fail-open on an empty snapshot."""
    catalogue = ctx.deps.catalogue
    filters = list(filters or [])
    if not catalogue.metrics or not catalogue.supported_dimensions_for(metric_id):
        return metric_id, dimensions, filters, {}, [], None

    def notes_for(renames: dict[str, str]) -> list[str]:
        return [f"'{a}' is answered by its conformed dimension '{b}' (same values on this metric's view)."
                for a, b in renames.items()]

    fitted, fitted_filters, renames, missing, period, value_gaps = _fit_to_metric(
        catalogue, metric_id, dimensions, filters
    )
    if not missing:
        return metric_id, fitted, fitted_filters, renames, notes_for(renames), period
    # a grain twin answers a slice this view does not have — never a value its own slice cannot hold (that
    # twin would be a different measure answering a different question: live harness, net_profit for an
    # email platform went to the product-line gross profit)
    for twin in [] if value_gaps else catalogue.grain_twins_for(metric_id):
        t_dims, t_filters, t_renames, t_missing, t_period, _ = _fit_to_metric(catalogue, twin, dimensions, filters)
        if t_missing:
            continue
        t_filters = [f.model_copy(update={"dimension": twin}) if f.dimension == metric_id else f for f in t_filters]
        label = catalogue.label_for(twin) or twin
        note = (
            f"'{metric_id}' is not stored at the grain of {', '.join(repr(k) for k in missing)}, so this is its "
            f"catalogue grain twin '{twin}' ({label}) — the same measure at that grain (its definition may differ "
            f"in detail; see get_metric_definition). Name it '{twin}' in the answer."
        )
        return twin, t_dims, t_filters, t_renames, [note, *notes_for(t_renames)], t_period
    supported = set(catalogue.supported_dimensions_for(metric_id))
    for key in missing:
        alternatives = [m for m in catalogue.metrics_supporting_dimension(key, like=metric_id) if m != metric_id]
        if not alternatives:
            continue  # nothing supports it — not a wrong pick, Cube decides
        raise ModelRetry(
            f"'{metric_id}' does not support the '{key}' dimension (it supports: "
            f"{', '.join(sorted(supported))}). For a breakdown/filter by '{key}', "
            f"use one of these metrics instead: {', '.join(alternatives)}. "
            f"Re-resolve and retry with a compatible metric."
        )
    return metric_id, fitted, fitted_filters, renames, notes_for(renames), period


# Entities kept when an unranked categorical breakdown crossed with a time grain is too large to
# read: the top K over the whole period, each with its full series.
_TOP_K_SERIES = 10


async def _rank_large_breakdown(
    ctx: RunContext[SelericDeps],
    metric_id: str,
    breakdown: list[str],
    grain: str,
    order: str | None,
    limit: int | None,
    rows: list[dict[str, Any]],
    dimensions: dict[str, Any],
    filters: list[dict[str, Any]],
    start: str,
    end: str,
    supports_brand: bool,
) -> tuple[list[dict[str, Any]], str] | None:
    """Answer an unranked breakdown that returned more groups than the model can read, instead of
    refusing it (live 2026-10-05: "CTR by campaign per day" -> 151 campaigns -> UNSUPPORTED_QUERY, and the
    user got no data although ClickHouse had all of it).

    * No time grain: re-issue the SAME query ranked by the metric (server-side, desc) — every row is
      kept in evidence, the summary shows the head of a real leaderboard rather than Cube's arbitrary order.
    * Crossed with a time grain: a single ranked query cannot give "top per bucket", and a global limit
      drops buckets (Case B below). So rank the ENTITIES over the whole period with one server-side query
      (grain none, desc, limit K) — by the metric itself when additive, else by the catalogue's
      ``volume_metric`` (CTR by impressions: a 1-impression campaign must not top a CTR board) — and
      keep every bucket of those K entities. Values are Cube's; nothing is summed or re-sorted here.

    None (fall through to the guard) when the shape is not a large unranked categorical breakdown, the
    caller listed the entities, or the ranking query fails."""
    catalogue = ctx.deps.catalogue
    if not catalogue.metrics or order is not None or limit is not None or len(rows) <= _MAX_SERIES_IN_SUMMARY:
        return None
    cat_dims = [k for k in breakdown if not catalogue.is_time_dimension(k)]
    if not cat_dims or any(isinstance(dimensions.get(k), (list, tuple, set)) for k in cat_dims):
        return None
    time_crossed = grain != "none"
    additive = (catalogue.aggregation_for(metric_id) or "additive") == "additive"
    rank_by = metric_id if (additive or not time_crossed) else (catalogue.volume_metric_for(metric_id) or metric_id)
    ranked_args = build_metrics_query_args(
        measure=rank_by,
        start=start,
        end=end,
        grain=None,
        dimensions=cat_dims,
        filters=filters or None,
        sort=[{"field": rank_by, "direction": "desc"}],
        limit=None if not time_crossed else _TOP_K_SERIES,
        inject_default_brand=supports_brand,
    )
    ranked = await _cached_metrics_query(ctx, ranked_args)
    ranked_rows = ranked.get("rows") or []
    if ranked.get("error") or not ranked_rows:
        return None
    dims = ", ".join(cat_dims)

    def key(row: dict[str, Any]) -> tuple[str, ...]:
        return tuple(str(dimension_value(row, d)) for d in cat_dims)

    if not time_crossed:
        return ranked_rows, (
            f"[{len(ranked_rows)} {dims} groups, ranked by {metric_id} (desc, server-side); every row is in "
            f"evidence — for a shorter board re-issue with limit=K.]"
        )
    top = [key(r) for r in ranked_rows[:_TOP_K_SERIES]]
    keep = set(top)
    kept = [r for r in rows if key(r) in keep]
    n_groups = len({key(r) for r in rows})
    if not kept:
        return None
    names = "; ".join(", ".join(k) for k in top)
    basis = metric_id if rank_by == metric_id else f"{rank_by} (the volume behind {metric_id})"
    return kept, (
        f"[{n_groups} {dims} groups over {grain}s is too many to read, so this is the top {len(top)} by "
        f"total {basis} over {start}..{end} (ranked server-side), each with its full {grain} series: {names}. "
        f"The other {max(n_groups - len(top), 0)} are omitted — list them in dimensions to see them.]"
    )


def _reject_unsupported_breakdown_shape(
    ctx: RunContext[SelericDeps],
    metric_id: str,
    breakdown: list[str],
    grain: str,
    order: str | None,
    limit: int | None,
    row_count: int,
    dimensions: dict[str, Any] | None = None,
) -> ToolResult | None:
    """Refuse two breakdown shapes a single Cube query answers wrong, steering to the
    executions that answer them right. Structure-driven, not keyword/metric-driven:
    keys on a NON-TIME breakdown dim (catalogue is_time separates a legitimate time
    series from a category), whether a time grain is crossed in, and the summary cap.
    Fail-open when the snapshot is empty (is_time can't be trusted) or there is no
    categorical breakdown to guard.

    Returns a failed ToolResult (never raises ModelRetry): the correct fix — a per-bucket
    fan-out — is a multi-step action the model may not satisfy on the first retry, and a
    raised ModelRetry that exhausts the retry budget becomes an UnexpectedModelBehavior
    crash (surfaces as V3_AGENT_FAILED). A graceful failure delivers the same guidance
    without burning retries toward a hard failure; matches _reject_unknown_metric's idiom.

    Case B — per-group top-N via a global limit (live MS3-a50cf03a1a): a categorical
    breakdown crossed with a time grain PLUS a limit cannot mean "top-N per bucket" —
    Cube applies the limit to the whole (category x bucket) grid and silently drops
    buckets (that trace's per-month query returned the 5 biggest cells overall; May and
    June vanished and were mis-narrated as "no returns"). Fires regardless of row count.

    Case A — large unranked dump (live MS3-848d29f41a): a categorical breakdown with no
    ranking and more rows than the model can read, which it would then hand-rank (dropped
    a month, named no product). Steer to order/limit, or a per-bucket fan-out."""
    catalogue = ctx.deps.catalogue
    if not catalogue.metrics:
        return None  # empty snapshot: can't trust is_time — let it through
    cat_dims = [k for k in breakdown if not catalogue.is_time_dimension(k)]
    if not cat_dims:
        return None  # a pure time series (grain-only / date breakdown) — nothing to guard
    dims = ", ".join(cat_dims)
    time_crossed = grain != "none" or any(catalogue.is_time_dimension(k) for k in breakdown)
    bucket = grain if grain != "none" else "period"

    if time_crossed and limit is not None:
        return ToolResult(
            success=False,
            summary=(
                f"query_metrics({metric_id}): breaking '{metric_id}' down by '{dims}' at "
                f"{bucket} grain with limit={limit} cannot give the top per {bucket} — Cube "
                f"applies the limit to the whole result, not within each {bucket}, so it "
                f"silently drops buckets. For the top per {bucket}, issue one ranked query "
                f"per bucket (a per-period filter) or use run_python over the evidence. For "
                f"an overall leaderboard instead, drop the grain."
            ),
            error_code="UNSUPPORTED_QUERY",
            retryable=False,
        )

    dims_dict = dimensions or {}
    all_dims_filtered = bool(cat_dims) and all(
        isinstance(val := dims_dict.get(k), (list, tuple, set)) and len(val) <= _MAX_SERIES_IN_SUMMARY
        for k in cat_dims
    )
    if order is None and limit is None and row_count > _MAX_SERIES_IN_SUMMARY and not all_dims_filtered:
        if time_crossed:
            summary = (
                f"query_metrics({metric_id}): the '{dims}' breakdown across {bucket}s returned {row_count} "
                f"groups — too many to report directly. To rank an overall leaderboard, drop the grain "
                f"(set grain='none', order='desc' or 'asc', limit=K). To see trends for specific entities, "
                f"filter by those entities in dimensions (e.g. dimensions={{'{dims}': ['val1', 'val2', ...]}}). "
                f"Do not add limit with grain={bucket}, as limit applies globally across all {bucket}s."
            )
        else:
            summary = (
                f"query_metrics({metric_id}): the '{dims}' breakdown returned {row_count} "
                f"groups — too many to report directly, and it is not ranked. Rank it: set "
                f"order='desc' (top) or 'asc' (bottom) and limit=K for a leaderboard. "
                f"Re-issue a ranked, bounded query — do not sort or aggregate the rows by hand."
            )
        return ToolResult(
            success=False,
            summary=summary,
            error_code="UNSUPPORTED_QUERY",
            retryable=False,
        )
    return None


async def get_metric_definition(ctx: RunContext[SelericDeps], metric_id: str) -> ToolResult:
    """Fetch one metric's full catalogue definition (catalogue_get_metric)."""
    key = _definition_cache_key(metric_id)
    if (cached := ctx.deps.query_cache.peek(key)) is not None:
        # Already fetched this mission (here or via get_metric_definitions): a
        # cache hit costs no MCP call and must not spend the lookup budget.
        return ToolResult(
            success=True,
            summary=f"definition for {metric_id} (already fetched this mission)",
            provenance=ArtifactProvenance(source_metadata={"definition": cached}),
        )
    if (spent := _definition_budget_spent(ctx)) is not None:
        return spent
    if (unknown := await _reject_unknown_metric(ctx, metric_id)) is not None:
        return unknown
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
    ctx.deps.query_cache.set(key, result)
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
    ids = [str(m).strip() for m in (metric_ids or []) if str(m).strip()]
    if not ids:
        return ToolResult(
            success=False,
            summary="no metric ids given",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
        )
    cache = ctx.deps.query_cache
    definitions = {mid: d for mid in ids if (d := cache.peek(_definition_cache_key(mid))) is not None}
    missing = [mid for mid in ids if mid not in definitions]
    errors: dict[str, Any] = {}
    if missing:
        # Only unfetched ids cost a lookup; an all-cached batch is free.
        if (spent := _definition_budget_spent(ctx)) is not None:
            return _definitions_result(definitions, errors) if definitions else spent
        try:
            result = await ctx.deps.mcp_client.call(
                agent_id=_AGENT_ID,
                capability="seleric.catalogue_get_metrics",
                arguments={"metric_ids": missing},
            )
        except Exception as exc:
            return _mcp_error_result(exc)
        fetched = (result or {}).get("metrics") or {}
        errors = (result or {}).get("errors") or {}
        for mid, definition in fetched.items():
            cache.set(_definition_cache_key(mid), definition)
        definitions = {**definitions, **fetched}
    if not definitions:
        return ToolResult(
            success=False,
            summary=f"no catalogue definitions found for {ids}",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
        )
    return _definitions_result(definitions, errors)


def _definitions_result(definitions: dict[str, Any], errors: dict[str, Any]) -> ToolResult:
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


_EMPTY_LABELS = ("", "None", "null", "none", "NULL")


def _compares_with_today(ctx: RunContext[SelericDeps]) -> bool:
    """The question compares some window with today while today is still running."""
    windows = getattr(ctx.deps.required_scope, "windows", ()) or ()
    return any(covers_in_progress_day(w.start, w.end, ctx.deps.as_of) for w in windows)


def _same_hours_applies(
    ctx: RunContext[SelericDeps],
    metric_id: str,
    period_start: datetime,
    period_end: datetime,
    grain: str,
    order: str | None,
    limit: int | None,
) -> bool:
    """A complete-days total of an additive metric, in a question that compares it with
    today: it also gets the same-hours figure (``_elapsed_metrics``), so the like-for-like
    number is in front of the model without it having to ask. Live 2026-10-07
    (MS3-371b639a68): told to re-query with elapsed_only, the model compared 3-day
    totals with half a day anyway and reported "lower spend today" while spend was
    32% ahead. Rankings keep their own entity set, so they are left alone."""
    return (
        grain == "none"
        and order is None
        and not limit
        and ctx.deps.catalogue.aggregation_for(metric_id) == "additive"
        and not covers_in_progress_day(period_start.date(), period_end.date(), ctx.deps.as_of)
        and _compares_with_today(ctx)
    )


def _in_progress_note(
    ctx: RunContext[SelericDeps],
    metric_id: str,
    period_start: datetime,
    period_end: datetime,
    *,
    same_hours: bool = False,
) -> str | None:
    """Warn that an additive total is not comparable with today's partial day: either
    it includes today, or the question compares it with today (and the same-hours
    figure is not attached)."""
    as_of = ctx.deps.as_of
    if ctx.deps.catalogue.aggregation_for(metric_id) != "additive":
        return None
    hours = completed_hours(as_of)
    if covers_in_progress_day(period_start.date(), period_end.date(), as_of):
        return (
            f"NOTE: {as_of.date()} is today and still in progress (about {hours} of 24 hours "
            "elapsed), so this total covers only part of that day. Do not compare it with "
            "complete days or with a multi-day total — re-query every compared period with "
            "elapsed_only=True, or say plainly that today is still running."
        )
    # Live 2026-10-07 (MS3-5bb148f278): today was fetched with elapsed_only but the
    # 3-day reference was not, so full days sat next to half a day.
    if not same_hours and _compares_with_today(ctx):
        return (
            f"NOTE: the question compares this window with today ({as_of.date()}), which is still "
            f"in progress (about {hours} of 24 hours elapsed). These are full-day totals: for the "
            "comparison, query this window with elapsed_only=True as well so both count the same hours."
        )
    return None


def _row_hour(row: dict[str, Any]) -> datetime | None:
    """The hour bucket of a grain='hour' row (Cube names it ``<dimension>.hour``)."""
    for key, value in row.items():
        if str(key).endswith(".hour") and isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                continue
    return None


async def _elapsed_metrics(
    ctx: RunContext[SelericDeps],
    *,
    metric_id: str,
    dimensions: dict[str, Any],
    breakdown: list[str],
    filters: list[dict[str, Any]],
    grain: str,
    period_start: datetime,
    period_end: datetime,
    order: str | None,
    limit: int | None,
    supports_brand: bool,
) -> ToolResult:
    """``query_metrics(elapsed_only=True)``: each day of the period counted only
    over the hours already elapsed today, so complete days compare like for like
    with today. One hourly Cube query; the tool, not the model, adds the hours."""
    as_of = ctx.deps.as_of
    aggregation = ctx.deps.catalogue.aggregation_for(metric_id)
    if aggregation != "additive":
        return ToolResult(
            success=False,
            summary=(
                f"elapsed_only needs an additive metric; {metric_id} is "
                f"{aggregation or 'not declared additive'}. Compare its additive parts with "
                "elapsed_only=True, or compare the ratio as is and say today is still running."
            ),
            error_code="UNSUPPORTED_QUERY",
            retryable=False,
        )
    if in_progress_day(as_of) is None:
        return ToolResult(
            success=False,
            summary=(
                f"elapsed_only compares against today's elapsed hours, but {as_of.date()} is "
                "complete — query without it."
            ),
            error_code="UNSUPPORTED_QUERY",
            retryable=False,
        )
    cutoff = completed_hours(as_of)
    if cutoff == 0:
        return ToolResult(
            success=False,
            summary="today has no complete hour yet — say the day has only just started.",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
        )
    args = build_metrics_query_args(
        measure=metric_id,
        start=period_start.date().isoformat(),
        end=period_end.date().isoformat(),
        grain="hour",
        dimensions=breakdown or None,
        filters=filters or None,
        inject_default_brand=supports_brand,
    )
    result = await _cached_metrics_query(ctx, args)
    if result.get("error"):
        return ToolResult(
            success=False,
            summary=(
                f"query_metrics({metric_id}, elapsed_only) failed: {result['error']}. Without an "
                "hourly series, compare per-day averages and say today is still running."
            ),
            error_code="UNSUPPORTED_QUERY",
            retryable=False,
        )
    first_day, last_day = period_start.date(), period_end.date()
    by_day = grain == "day"
    totals: dict[tuple[str, tuple[str, ...]], float] = {}
    unlabelled = 0.0
    for row in result.get("rows") or []:
        hour = _row_hour(row)
        value = row.get(metric_id)
        if hour is None or value is None or hour.hour >= cutoff:
            continue
        if not first_day <= hour.date() <= last_day:
            continue
        labels = tuple(str(dimension_value(row, k)) for k in breakdown)
        if any(v in _EMPTY_LABELS for v in labels):
            unlabelled += float(value)
            continue
        key = (hour.date().isoformat() if by_day else "", labels)
        totals[key] = totals.get(key, 0.0) + float(value)
    if not totals:
        return ToolResult(
            success=False,
            summary=f"no data for {metric_id} over {first_day}..{last_day} before {cutoff:02d}:00",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
        )
    ranked = list(totals.items())
    if order in ("desc", "asc"):
        ranked.sort(key=lambda kv: kv[1], reverse=order == "desc")
    if limit:
        ranked = ranked[: max(1, int(limit))]
    n_days = (last_day - first_day).days + 1
    through = f"{cutoff:02d}:00"
    fixed = {k: (",".join(map(str, v)) if isinstance(v, list) else str(v)) for k, v in dimensions.items() if v}
    provenance_data: dict[str, Any] = result.get("provenance") or {}
    currency = str(provenance_data.get("currency") or "").strip()
    provenance = ArtifactProvenance(
        query_version=str(provenance_data.get("query_id") or ""), source_metadata=provenance_data
    )
    known = _evidence_index(ctx)
    artifact_ids: list[str] = []
    parts: list[str] = []
    for (day, labels), total in ranked:
        start = datetime.fromisoformat(day).replace(tzinfo=period_start.tzinfo) if day else period_start
        end = start if day else period_end
        row_dims = {**fixed, **dict(zip(breakdown, labels, strict=True)), ELAPSED_KEY: through}
        evidence = EvidenceArtifact(
            metric_id=metric_id,
            dimensions=row_dims,
            grain="day" if day else "none",
            as_of=as_of,
            period_start=start,
            period_end=end,
            value=total,
            unit=currency or None,
            source_query=args,
        )
        artifact_ids.append(
            _put_evidence(
                ctx,
                evidence,
                index=known,
                raw_id=f"raw:{metric_id}:{start.date()}:{end.date()}:{through}",
                provenance=provenance,
            )
        )
        label = " | ".join(p for p in (day, ", ".join(f"{k}={v}" for k, v in zip(breakdown, labels, strict=True))) if p)
        per_day = "" if day or n_days == 1 else f" (per-day average {total / n_days:.2f} over {n_days} days)"
        parts.append(f"{label or 'total'}={total}{per_day}")
    summary = (
        f"{metric_id} over {first_day}..{last_day}, counting only 00:00-{through} of each day "
        f"(the hours elapsed today), so every day is comparable with today — use these exact "
        f"values: {'; '.join(parts[:_MAX_SERIES_IN_SUMMARY])}"
        + (f"; …(+{len(parts) - _MAX_SERIES_IN_SUMMARY} more rows in evidence)" if len(parts) > _MAX_SERIES_IN_SUMMARY else "")
        + ". Compare per-day figures, never a multi-day total with one day."
    )
    if unlabelled:
        # Live 2026-10-07 (MS3-5bb148f278): net sales broken down by campaign AND ad set
        # dropped every Google order (no ad set), and the answer reported those
        # campaigns at 0 sales beside their own non-zero order counts.
        summary += (
            f" {unlabelled:g} of the period's {metric_id} has no {' / '.join(breakdown)} value and is "
            "not listed above — a missing row is not zero; drop the finer breakdown to count it."
        )
    ctx.deps.scratchpad.note(summary)
    return ToolResult(success=True, artifact_ids=artifact_ids, summary=summary, provenance=provenance)


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
    dimensions: dict[str, DimensionValue] | None = None,
    filters: list[MetricFilter] | None = None,
    grain: str = "none",
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    order: str | None = None,
    limit: int | None = None,
    pool_listed_values: bool = False,
    elapsed_only: bool = False,
) -> ToolResult:
    """The only path to a numeric metric value. Writes one EvidenceArtifact.

    ``dimensions`` / periods default empty-or-as_of so the model can look up
    "gross sale" without inventing a brand filter or a training-data year.

    A dimension value filters to that value; an empty string breaks the result
    down by that dimension; a list keeps only those values AND returns one row
    (one series, with ``grain``) per value — e.g. the daily trend of the top 5
    entities is ``dimensions={"<entity_name_dimension>": [<the 5 names>]}, grain="day"``.
    Always group by the entity's human-readable name dimension rather than its ID.
    Add the entity's name dimension as an empty breakdown to label the rows.
    Set ``pool_listed_values=True`` only when the listed values are spellings of ONE
    group (``utm_medium`` whatsapp/wa) and you want a single pooled number.

    ``filters`` are structured conditions beyond "equals": ``{"dimension": d, "operator": op,
    "values": [...]}`` with op one of equals, notEquals (exclude), contains, notContains,
    startsWith, endsWith, gt, gte, lt, lte, set (has a value), notSet (empty). A filter whose
    ``dimension`` is this metric's own id (or another metric of its view) keeps only the rows
    whose aggregated value passes the comparison — e.g. entities of a breakdown above a threshold.

    Dimensions are conformed across views: the same dimension id (a platform, a channel, a
    campaign) works on every metric that can carry it; when a metric's own view stores it under a
    sibling name, or only its catalogue grain twin carries the slice, the tool answers there and says
    so in the summary — use the metric id the summary names.

    For a time series (monthly/weekly/daily trend) set ``grain`` — do NOT pass
    the date dimension (``refund_date``/``report_date``/…) as a breakdown; that
    is the time axis and ``grain`` already buckets it. Never sum a returned
    series by hand; re-query at the grain you need and report the tool's rows.

    For a top/bottom-N ranking, break down by the entity dimension (empty
    value, e.g. ``dimensions={"product_title": ""}``), set ``order="desc"``
    (top/most/highest) or ``"asc"`` (bottom/least/lowest), and ``limit=N``.
    That is one call — do not fetch every row and sort client-side.

    Today is still in progress, so a period that includes today is not
    comparable with complete days. To compare today with earlier days, set
    ``elapsed_only=True`` on EVERY period you compare: each day then counts only
    the hours already elapsed today (same hours, like for like), and the result
    states the per-day average for a multi-day period. Additive metrics only;
    for a ratio, compare its additive parts this way.
    """
    if (unknown := await _reject_unknown_metric(ctx, metric_id)) is not None:
        return unknown
    dimensions = _sanitize_dimensions(dimensions)
    # Only lists the model wrote compare entities; a concept's bound filter is
    # a definition ("WhatsApp" = utm_medium whatsapp|wa) and stays pooled.
    asked_lists = {k for k, v in dimensions.items() if isinstance(v, list)}
    if bound_filter := ctx.deps.query_cache.peek(f"concept_filter:{metric_id}"):
        if isinstance(bound_filter, dict):
            for k, v in bound_filter.items():
                if k not in dimensions:
                    dimensions[k] = v
    asked_metric = metric_id
    metric_id, dimensions, structured_filters, renamed, conform_notes, named_period = _conform_dimensions(
        ctx, metric_id, dimensions, [MetricFilter.model_validate(f) for f in (filters or [])]
    )
    asked_lists = {renamed.get(k, k) for k in asked_lists}  # a renamed list is still the model's entity list
    structured_filters = _fold_equals_filters(ctx, dimensions, structured_filters)
    # the twin answers under its own id: an earlier concept filter bound to it applies as well
    twin_filter = ctx.deps.query_cache.peek(f"concept_filter:{metric_id}") if metric_id != asked_metric else None
    if isinstance(twin_filter, dict):
        for k, v in twin_filter.items():
            dimensions.setdefault(k, v)
    if named_period is not None and period_start is None and period_end is None:
        tz = ctx.deps.as_of.tzinfo
        period_start = datetime.combine(named_period[0], datetime.min.time(), tz)
        period_end = datetime.combine(named_period[1], datetime.min.time(), tz)
    window_note: str | None = None
    if (pinned := _pin_to_resolved_window(ctx, period_start, period_end)) is not None:
        period_start, period_end, window_note = pinned
    window = ctx.deps.resolved_window
    if window is not None and period_start is None and period_end is None:
        # The question named a period; use it rather than collapsing to as_of,
        # which is today and therefore still empty while the day is in flight.
        if window.start and window.end:
            period_start = datetime.fromisoformat(window.start).replace(tzinfo=ctx.deps.as_of.tzinfo)
            period_end = datetime.fromisoformat(window.end).replace(tzinfo=ctx.deps.as_of.tzinfo)
    period_end = period_end or ctx.deps.as_of
    period_start = period_start or period_end
    breakdown = [k for k, v in dimensions.items() if not v]
    # A list of several values is a set of entities to compare, so each keeps
    # its own row. Pooled, "CTR trend of the top 5 campaigns" came back as one
    # blended line labelled with five ids, and the model then attached daily
    # values to campaign names the evidence never carried (live 2026-10-04
    # MS3-c645523b51, MS3-23de4a7094). Brand lists stay a filter (one brand
    # scope), time dimensions are the grain's job.
    if not pool_listed_values:
        breakdown += [
            k
            for k, v in dimensions.items()
            if k in asked_lists
            and isinstance(v, list)
            and len(v) > 1
            and _normalize_dim_token(k) not in _BRAND_DIM_KEYS
            and not ctx.deps.catalogue.is_time_dimension(k)
        ]
    # A time-axis dimension (catalogue is_time: order_date, refund_date, …)
    # requested as a breakdown is NOT a categorical group-by. Cube buckets time
    # via `granularity`; sending the raw date dimension as a group-by instead
    # yields one row per raw day AND overrides `grain` (live MS3-99ad433e18:
    # grain=month + refund_date breakdown -> 146 daily rows the model then
    # hand-summed into wrong monthly totals; the raw dim also has no granularity
    # suffix, so row_date() can't label it). Fold it into the grain: honor an
    # explicit grain, else default to day. Catalogue-driven, not name-matched;
    # empty snapshot -> no-op (fail-open, Cube stays authority).
    if date_breakdown := [k for k in breakdown if ctx.deps.catalogue.is_time_dimension(k)]:
        breakdown = [k for k in breakdown if k not in date_breakdown]
        if grain == "none":
            grain = "day"
    filters = [
        {"dimension": k, "operator": "equals", "values": list(v) if isinstance(v, list) else [v]}
        for k, v in dimensions.items()
        if v and not ctx.deps.catalogue.is_time_dimension(k)
    ]
    filters += [f.model_dump() for f in structured_filters]
    # Rows with an empty breakdown value are dropped when evidence is written (below), so exclude them
    # in the query: otherwise a ranked limit is spent on them first (live 2026-10-06: net_profit by
    # campaign_name x sub_channel, limit=5 -> 4 of the top 5 had no campaign, 1 row survived).
    filters += [
        {"dimension": k, "operator": "set", "values": []}
        for k in breakdown
        if not ctx.deps.catalogue.is_time_dimension(k) and _normalize_dim_token(k) not in _BRAND_DIM_KEYS
    ]
    sort = _top_n_sort(metric_id, order)
    # Only scope to the default brand for metrics that actually carry a brand
    # dimension (catalogue-driven, not a hardcoded metric list): injecting a
    # brand filter onto a brand-less metric would make Cube reject the query.
    # Fail-open when the snapshot is empty.
    supports_brand = (not ctx.deps.catalogue.metrics) or bool(
        {d.lower() for d in ctx.deps.catalogue.supported_dimensions_for(metric_id)} & _BRAND_DIM_KEYS
    )
    same_hours = _same_hours_applies(ctx, metric_id, period_start, period_end, grain, order, limit)
    if elapsed_only:
        return await _elapsed_metrics(
            ctx,
            metric_id=metric_id,
            dimensions=dimensions,
            breakdown=breakdown,
            filters=filters,
            grain=grain,
            period_start=period_start,
            period_end=period_end,
            order=order,
            limit=limit,
            supports_brand=supports_brand,
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

    result = None
    _cache_eligible = (
        grain == "none"
        and not breakdown
        and not filters
        and limit is None
        and not pool_listed_values
    )
    if _cache_eligible:
        from seleric_swarm.services.domain_health.snapshot_store import SnapshotStore
        import logging
        _log = logging.getLogger(__name__)
        try:
            cached = await SnapshotStore().afind_metric(metric_id)
            if cached is not None:
                resolved, snapshot = cached
                snap_start = snapshot.window.get("start")
                snap_end = snapshot.window.get("end")
                if snap_start == period_start.date().isoformat() and snap_end == period_end.date().isoformat():
                    _log.debug("query_metrics ready_store hit metric_id=%s as_of=%s", metric_id, snapshot.as_of)
                    result = {
                        "rows": [{metric_id: resolved.value}],
                        "provenance": {
                            "source": "ready_store",
                            "as_of": resolved.freshness,
                            "snapshot_as_of": snapshot.as_of,
                        }
                    }
        except Exception as e:
            _log.debug("query_metrics ready_store miss metric_id=%s reason=%s", metric_id, str(e))
            
    if result is None:
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
    not_found: list[dict[str, Any]] = result.get("value_not_found") or []  # type: ignore
    if not_found:
        # Cube answers a filter on a value that never occurs with a 0 row; the
        # gateway flags it so "0" is never reported as a measured count (live:
        # channel=whatsapp → "0 orders" while 35 were attributed via utm_medium).
        parts: list[str] = []
        for miss in not_found:
            where = "; ".join(
                f"{f.get('dimension')} = {', '.join(f.get('values') or [])}"
                for f in miss.get("found_in") or []
            )
            parts.append(
                f"{', '.join(miss.get('values') or [])} is not a value of {miss.get('dimension')} "
                f"for {metric_id}"
                + (f"; the data records it in: {where}" if where else "; it is not recorded anywhere")
            )
        return ToolResult(
            success=False,
            summary=(
                f"query_metrics({metric_id}): " + ". ".join(parts) + ". This is not a zero count — "
                "re-query with a metric that supports one of those dimensions, or tell the user "
                "the value is not recorded."
            ),
            error_code="VALUE_NOT_FOUND",
            retryable=False,
        )
    rows: list[dict[str, Any]] = result.get("rows") or []  # type: ignore
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
            f for f in filters
            if _normalize_dim_token(f["dimension"]) not in _BRAND_DIM_KEYS and f.get("operator") != "set"
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
    # Before persisting: refuse breakdown shapes a single query answers wrong (a large
    # unranked dump, or a per-group top-N expressed as a global limit) with a graceful
    # failed result — no artifacts written, no ModelRetry (which could exhaust into a crash).
    rank_note: str | None = None
    if (picked := await _rank_large_breakdown(
        ctx, metric_id, breakdown, grain, order, limit, rows, dimensions, filters,
        period_start.date().isoformat(), period_end.date().isoformat(), supports_brand,
    )) is not None:
        rows, rank_note = picked
    if rank_note is None and (blocked := _reject_unsupported_breakdown_shape(
        ctx, metric_id, breakdown, grain, order, limit, len(rows), dimensions=dimensions
    )) is not None:
        return blocked
    provenance_data: dict[str, Any] = result.get("provenance") or {}  # type: ignore
    provenance = ArtifactProvenance(
        query_version=str(provenance_data.get("query_id") or ""),
        source_metadata=provenance_data,
    )

    async def _write_evidence() -> ToolResult:
        # grain="none" -> Cube returns one period-aggregate row. A real grain
        # ("day"/"week"/"month") -> one row per bucket; each bucket is its own
        # EvidenceArtifact (rule 6/7: one artifact per fetched fact, not a
        # multi-day value folded into a single artifact) — needed so a
        # day-granularity series (e.g. feeding a causal/anomaly consumer) is
        # immutable evidence per day, not one mutable blob.
        per_row_dates = [row_date(row) for row in rows] if grain != "none" else [None] * len(rows)
        currency = str(provenance_data.get("currency") or "").strip()
        known_evidence = _evidence_index(ctx)
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
            row_dimensions: dict[str, str] = {}
            for k, v in dimensions.items():
                if not v:
                    continue
                row_val = dimension_value(row, k)
                if row_val is not None and str(row_val) not in ("", "None", "null", "none", "NULL"):
                    row_dimensions[k] = str(row_val)
                elif isinstance(v, (list, tuple, set)):
                    row_dimensions[k] = ",".join(str(x) for x in v) if len(v) > 1 else str(list(v)[0])
                else:
                    row_dimensions[k] = str(v)
            for key in breakdown:
                raw = str(dimension_value(row, key))
                # Behavioral: drop rows with empty/unmapped breakdown values
                # rather than letting them leak into labels or aggregate under
                # a false "None" category. Applies to any breakdown dimension.
                if raw in ("", "None", "null", "none", "NULL"):
                    break
                row_dimensions[key] = raw
            else:
                # Only build evidence/label when all breakdown values resolved
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
                artifact_ids.append(
                    _put_evidence(
                        ctx,
                        evidence,
                        index=known_evidence,
                        raw_id=f"raw:{metric_id}:{bucket_start.date()}:{bucket_end.date()}",
                        provenance=provenance,
                    )
                )
                # Label: the time bucket AND the breakdown dimension values, both when
                # present. A breakdown+grain query previously dropped the category
                # from the label, making rows indistinguishable. Preserve it.
                time_label = ""
                if bucket_date and grain in {"week", "month"}:
                    time_label = f"{bucket_start.date()}..{bucket_end.date()}"
                    clip_start = max(bucket_start.date(), period_start.date())
                    clip_end = min(bucket_end.date(), period_end.date())
                    if (clip_start, clip_end) != (bucket_start.date(), bucket_end.date()):
                        time_label += f" (PARTIAL {grain}: only {clip_start}..{clip_end})"
                elif bucket_date:
                    time_label = bucket_date
                dim_label = ", ".join(
                    f"{k}={v}"
                    for k, v in row_dimensions.items()
                    if not ctx.deps.catalogue.is_time_dimension(k)
                )
                label = " | ".join(p for p in (time_label, dim_label) if p) or f"{bucket_start.date()}..{bucket_end.date()}"
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
        # The unit, so the model never guesses the currency (golden 2026-10-08: INR answered as "$" / "USD"):
        # the query's currency when the catalogue says the metric is in it, never for counts or ratios.
        unit = f" {currency}" if currency and ctx.deps.catalogue.unit_for(metric_id) == currency else ""
        if len(series) == 1:
            summary = f"{metric_id}={_fmt_value(series[0]['value'])}{unit} over {period_start.date()}..{period_end.date()}"
        else:
            shown = series[:_MAX_SERIES_IN_SUMMARY]
            body = "; ".join(f"{s['label']}={_fmt_value(s['value'])}{unit}" for s in shown)
            more = "" if len(series) <= _MAX_SERIES_IN_SUMMARY else f"; …(+{len(series) - _MAX_SERIES_IN_SUMMARY} more rows in evidence)"
            summary = (
                f"{metric_id} over {period_start.date()}..{period_end.date()} "
                f"({len(series)} rows) — use these exact values: {body}{more}."
                + _series_stats(ctx, metric_id, [float(s["value"]) for s in series])
            )
        if rank_note:
            summary = f"{rank_note} {summary}"
        if conform_notes:
            summary = f"[{' '.join(conform_notes)}] {summary}"
        if note := _in_progress_note(ctx, metric_id, period_start, period_end, same_hours=same_hours):
            summary = f"{summary} {note}"
        prov = ArtifactProvenance(
            query_version=provenance.query_version,
            source_metadata={**(provenance.source_metadata or {}), "series": series},
        )
        # Working memory: record the established value so the model re-reads it
        # next turn instead of re-issuing this query (restates the summary it
        # already holds — not a new number, so rule 6's evidence chain is
        # untouched). In-process append; no I/O, no added latency.
        ctx.deps.scratchpad.note(summary)
        api_warnings: list[str] = result.get("warnings") or []  # type: ignore
        return ToolResult(
            success=True,
            artifact_ids=artifact_ids,
            summary=summary,
            provenance=prov,
            warnings=[*api_warnings, *([window_note] if window_note else [])],
        )

    # A cache HIT above means the same fetch already ran this mission — but
    # every call to this function still reached this point and would write a
    # fresh, duplicate set of EvidenceArtifacts for identical rows (live
    # 2026-09-21: a 200-row product_title breakdown was called twice 18s
    # apart, doubling the evidence the model had to re-read next turn).
    # Cache the built ToolResult too, so a repeat call reuses the same
    # artifact_ids instead of writing them again.
    async def _build() -> ToolResult:
        base = await _write_evidence()
        if not (base.success and same_hours):
            return base
        companion = await _elapsed_metrics(
            ctx,
            metric_id=metric_id,
            dimensions=dimensions,
            breakdown=breakdown,
            filters=filters,
            grain=grain,
            period_start=period_start,
            period_end=period_end,
            order=None,
            limit=None,
            supports_brand=supports_brand,
        )
        if not companion.success:
            return base
        return base.model_copy(
            update={
                "artifact_ids": [*base.artifact_ids, *companion.artifact_ids],
                "summary": f"{base.summary} SAME HOURS AS TODAY — {companion.summary}",
            }
        )

    if not _QUERY_CACHE_ENABLED:
        return await _build()
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
        # Nudge on every repeat; withdraw the tool on the 2nd+ duplicate so
        # a model that ignores the nudge cannot loop indefinitely.
        # RepeatCallGuard also withdraws via wrap_tool_execute at WITHDRAW_AFTER
        # (3 identical calls) — this path handles direct callers (e.g. tests)
        # that bypass the capability wrapper.
        if dups >= 2:
            withdraw_tool(ctx.deps, "query_metrics")
        return prior.model_copy(
            update={
                "summary": (
                    f"ALREADY FETCHED — {prior.summary}. You have these values; "
                    "write your final_response now. Do NOT call query_metrics "
                    "for this metric/period again — it returns the same rows."
                )
            }
        )
    return await ctx.deps.query_cache.get_or_fetch(
        result_key,
        _build,
        # Deterministic failures stay cached; a retryable one (transient MCP or
        # Cube fault) must actually re-run when the model retries it.
        cacheable=lambda r: r.success or not r.retryable,
    )


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
    hierarchy: str | None = None,
    within: dict[str, str] | None = None,
    filters: list[MetricFilter] | None = None,
    order: str | None = None,
    limit: int | None = None,
) -> ToolResult:
    """Breakdown of ``metric_id`` by ``dimension`` over a period.

    ``filters`` scope the drill with structured conditions (same shape and operators as
    ``query_metrics``: notEquals to exclude, contains / startsWith, gt / lt on a metric's value, …).
    Dimensions are conformed across views like in ``query_metrics``: a dimension stored under a sibling
    name on this metric's view, or carried only by its catalogue grain twin, is answered there and the
    summary names what was used.

    Ranked drill: ``order`` ("desc" | "asc") with an optional ``limit`` returns the top / bottom
    rows by the metric itself (server-side sort + limit); rows with no activity (value 0) are left
    out of a ranking. Without ``order`` every row is kept, zeros included.

    A label dimension that declares a stable key in the catalogue (e.g. a title two different
    items can share) is grouped by key AND label, so same-named items stay separate rows.

    Hierarchy drill (semantic v2): pass ``hierarchy`` (traffic: platform → channel → sub_channel;
    geo; product; ``ad``: ad_platform → campaign_name → adset_name → ad_name; ``campaign``:
    campaign_name → adset_name → ad_name — both on every view that carries those levels, ad
    delivery and orders / sessions / refunds alike; the catalogue lists each hierarchy's views) with
    ``dimension="next"`` to go one
    level below what ``within`` pins (e.g. ``within={"platform": "meta"}`` → channels of Meta), or
    with ``dimension`` = a level of that hierarchy to jump to it. ``within`` values are equals
    filters on the coarser levels and scope the parent query.

    The live ``metrics_drilldown`` tool drills into a prior ``metrics_query``
    result by its ``parent_query_id`` — it has no metric/period-only form.
    This wrapper runs the parent query itself so the frozen tool signature
    (metric_id/dimension/period, no query-id bookkeeping) stays the agent's
    contract; that's orchestration of the live two-call API, not a new
    heuristic.
    """
    # Route before a doomed drill: a metric can only break down by a dimension
    # its own Cube view carries. Drilling refund_count (view refund_events) by
    # product_title returned "no rows" and the model deferred to a follow-up
    # (live MS3-99ad433e18) — instead, name the metric(s) whose view DOES support
    # it. Catalogue-driven; empty snapshot -> no guard (fail-open, Cube decides).
    within = {str(k): str(v) for k, v in (within or {}).items()}
    next_level = bool(hierarchy) and dimension in ("", "next")
    # Fit the drill to the catalogue first (conformed siblings, then the grain twin) — the same path as
    # query_metrics, so a drill never refuses a slice the warehouse can answer.
    probe: dict[str, DimensionValue] = {**within, **({} if next_level else {dimension: ""})}
    try:
        metric_id, _fitted, structured, renamed, conform_notes, _ = _conform_dimensions(
            ctx, metric_id, probe, [MetricFilter.model_validate(f) for f in (filters or [])]
        )
    except ModelRetry as exc:
        # a drill names the metrics that carry the slice as a result, not a retry (its caller re-plans)
        return ToolResult(success=False, summary=str(exc), error_code="UNSUPPORTED_QUERY", retryable=False)
    if not next_level:
        dimension = renamed.get(dimension, dimension)
    within = {renamed.get(k, k): str(v) for k, v in within.items()}
    structured = _fold_equals_filters(ctx, within, structured)  # type: ignore[arg-type]
    supported = ctx.deps.catalogue.supported_dimensions_for(metric_id)
    if supported and not next_level and dimension not in supported:
        alts = ctx.deps.catalogue.metrics_supporting_dimension(dimension)
        redirect = f"; use one of: {', '.join(alts)}" if alts else "; no available metric supports it"
        return ToolResult(
            success=False,
            summary=f"{metric_id} does not support a breakdown by {dimension}{redirect}",
            error_code="UNSUPPORTED_QUERY",
            retryable=False,
        )
    direction = (order or "").strip().lower() or None
    if direction not in (None, "asc", "desc"):
        return ToolResult(
            success=False,
            summary=f"order must be 'desc' or 'asc' (got {order!r})",
            error_code="UNSUPPORTED_QUERY",
            retryable=False,
        )
    # Group a label by its declared stable key too (catalogue-declared, only when this metric
    # can carry the key), so two items sharing a label never merge into one row.
    targets = [dimension]
    stable_key = None if next_level else ctx.deps.catalogue.stable_key_for(dimension)
    if stable_key and stable_key != dimension and (not supported or stable_key in supported):
        targets = [stable_key, dimension]
    # The server's drilldown inherits the parent's sort and limit, so the ranking applies to the
    # drilled rows (the parent itself is one total row).
    parent_args = build_metrics_query_args(
        measure=metric_id,
        start=period_start.date().isoformat(),
        end=period_end.date().isoformat(),
        filters=[{"dimension": k, "operator": "equals", "values": list(v) if isinstance(v, list) else [v]}
                 for k, v in within.items()] + [f.model_dump() for f in structured] or None,
        sort=[{"field": metric_id, "direction": direction}] if direction else None,
        limit=limit if limit and limit > 0 else None,
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
    if hierarchy:
        drilldown_args: dict[str, Any] = {"parent_query_id": parent_query_id, "hierarchy": hierarchy}
        if not next_level:
            drilldown_args["to_level"] = dimension
    else:
        drilldown_args = {"parent_query_id": parent_query_id, "target_dimensions": list(targets)}
    fetch_drilldown = lambda: ctx.deps.mcp_client.call(  # noqa: E731
        agent_id=_AGENT_ID, capability="seleric.metrics_drilldown", arguments=drilldown_args
    )
    try:
        if _QUERY_CACHE_ENABLED:
            result: dict[str, Any] = await ctx.deps.query_cache.get_or_fetch(
                _cache_key("seleric.metrics_drilldown", drilldown_args),
                fetch_drilldown,
                cacheable=lambda r: not (isinstance(r, dict) and r.get("error")),
            )
        else:
            result = await fetch_drilldown()
    except Exception as exc:
        return _mcp_error_result(exc)
    if hierarchy and isinstance(result, dict) and result.get("error"):
        return ToolResult(
            success=False,
            summary=f"hierarchy drill {hierarchy} for {metric_id}: {result['error']}",
            error_code="UNSUPPORTED_QUERY",
            retryable=False,
        )
    if hierarchy:
        # the server picked the level (next below what `within` pins); evidence is keyed by it
        drilled = ((result or {}).get("drilled_to") or {}).get("dimensions") or []
        dimension = drilled[-1] if drilled else dimension
        targets = [dimension]
    rows = (result or {}).get("rows") or []
    if not rows:
        return ToolResult(
            success=False,
            summary=f"no drilldown rows for {metric_id} by {dimension}",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
        )
    prov_data: dict[str, Any] = result.get("provenance") or {}  # type: ignore
    provenance = ArtifactProvenance(source_metadata=prov_data)
    known_evidence = _evidence_index(ctx)
    artifact_ids: list[str] = []
    lines: list[str] = []
    values: list[float] = []
    for row in rows:
        value = row.get(metric_id)
        if value is None:
            continue
        if direction and float(value) == 0:
            continue  # no activity: not a member of a top / bottom ranking
        row_dims = {d: str(dimension_value(row, d)) for d in targets}
        lines.append(f"{', '.join(f'{d}={v}' for d, v in row_dims.items())} -> {_fmt_value(float(value))}")
        values.append(float(value))
        evidence = EvidenceArtifact(
            metric_id=metric_id,
            dimensions={**{k: ",".join(v) if isinstance(v, list) else v for k, v in within.items()}, **row_dims},
            grain="none",
            as_of=ctx.deps.as_of,
            period_start=period_start,
            period_end=period_end,
            value=float(value),
            source_query={"parent_query_id": parent["query_id"], "target_dimensions": list(targets),
                          **({"hierarchy": hierarchy, "within": within} if hierarchy else {})},
        )
        artifact_ids.append(
            _put_evidence(
                ctx,
                evidence,
                index=known_evidence,
                raw_id=f"raw:{metric_id}:{':'.join(targets)}:{':'.join(row_dims.values())}",
                provenance=provenance,
            )
        )
    if not artifact_ids:
        return ToolResult(
            success=False,
            summary=f"drilldown rows for {metric_id} by {dimension} had no usable values",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
        )
    shown = lines[:_MAX_SERIES_IN_SUMMARY]
    more = "" if len(lines) <= _MAX_SERIES_IN_SUMMARY else f"; …(+{len(lines) - _MAX_SERIES_IN_SUMMARY} more rows in evidence)"
    scope = f" within {', '.join(f'{k}={v}' for k, v in within.items())}" if within else ""
    ranking = f", top {limit} by {metric_id} {direction}" if direction and limit else (
        f", ranked {direction}" if direction else "")
    summary = (
        f"{metric_id} by {', '.join(targets)}{scope} over {period_start.date()}..{period_end.date()} "
        f"({len(artifact_ids)} rows{ranking}) — use these exact values: {'; '.join(shown)}{more}."
        + _series_stats(ctx, metric_id, values)
    )
    if conform_notes:
        summary = f"[{' '.join(conform_notes)}] {summary}"
    ctx.deps.scratchpad.note(summary)
    return ToolResult(
        success=True,
        artifact_ids=artifact_ids,
        summary=summary,
        provenance=provenance,
    )
