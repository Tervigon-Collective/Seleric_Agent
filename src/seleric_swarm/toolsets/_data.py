"""Shared data-access helpers for the diagnosis, composition and exploration tools.

The per-mission MCP query cache, evidence artifacts for fetched series, unknown-metric rejection and the
live-data failure result. Failures come back as results the model reads; nothing here raises ``ModelRetry``, whose
per-tool retry cap would fail the whole turn if the model ignored it.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable
from typing import Any

from pydantic_ai import RunContext

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.semantic import McpSemanticPort
from seleric_swarm.services.mcp_query import (
    build_metrics_query_args,
    call_metrics_query,
    with_date_axis,
)

# Single agent identity for MCPGateway allowlisting: the runtime has one agent loop.
_AGENT_ID = "v3_agent"


def cache_key(capability: str, arguments: dict[str, Any]) -> str:
    """Deterministic key for ``SelericDeps.query_cache`` — same capability +
    same arguments always means the same fetch, so this is the one place
    that decides what "identical query" means for dedup purposes."""
    return f"{capability}:{json.dumps(arguments, sort_keys=True, default=str)}"


_QUERY_CACHE_ENABLED = True


async def cached_metrics_query(
    ctx: RunContext[SelericDeps], arguments: dict[str, Any]
) -> dict[str, Any]:
    import asyncio

    arguments = with_date_axis(arguments, ctx.deps.date_axis, ctx.deps.catalogue)
    key = cache_key("seleric.metrics_query", arguments)

    def call() -> Awaitable[dict[str, Any]]:
        return call_metrics_query(ctx.deps.mcp_client, agent_id=_AGENT_ID, arguments=arguments)

    def fetch() -> Awaitable[dict[str, Any]]:
        if not _QUERY_CACHE_ENABLED:
            return call()
        # call_metrics_query returns failures as {"error": ...} values; caching
        # one would replay a transient MCP fault for the rest of the mission,
        # even though fetch_failure tells the model it is retryable.
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
        return {
            "error": (
                f"Cube query budget exhausted for this turn ({verdict.reason}). "
                "Do not fetch any more data -- write the final answer now using "
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


def fetch_failure(what: str, error: Any) -> ToolResult:
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


# What makes two evidence rows the same fact. ``as_of``/``fetched_at`` are left
# out: a retry fetching the identical row a minute later is not new evidence.
_EVIDENCE_IDENTITY = (
    "metric_id", "dimensions", "grain", "period_start", "period_end", "value", "unit", "source_query",
)


def _evidence_identity(payload: dict[str, Any]) -> str:
    return json.dumps({k: payload.get(k) for k in _EVIDENCE_IDENTITY}, sort_keys=True, default=str)


def evidence_index(ctx: RunContext[SelericDeps]) -> dict[str, str]:
    """identity -> artifact id for the evidence this mission already holds."""
    index: dict[str, str] = {}
    for artifact in ctx.deps.artifact_store.list_for_mission(ctx.deps.mission_id):
        if artifact.artifact_type == "evidence" and isinstance(artifact.payload, dict):
            index.setdefault(_evidence_identity(artifact.payload), artifact.id)
    return index


def put_evidence(
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


async def resolve_concept(
    ctx: RunContext[SelericDeps],
    concept: str,
    axes: dict[str, str] | None = None,
) -> ToolResult:
    """Deterministically map a business concept to exactly ONE catalogue metric id.
    pass the concept in
    the user's own words (natural language, not a metric id) plus any axes their
    phrasing implies. The catalogue owns the axis vocabulary and defaults, so you
    never hard-code axis values; unspecified axes take their disclosed default.

    Returns the resolved ``metric_id`` (feed straight to ``metrics_query``), the
    ``axes`` applied and which were defaulted, and any ``filter`` the concept binds
    (pass it through to ``metrics_query`` unchanged). A draft target is flagged; an
    unsupported axis combination returns a reason plus nearest metrics (never a wrong
    sibling); an unmodelled concept returns suggestions. On unsupported/unknown, fall
    back to ``catalogue_search_metrics``. Resolution only — the id is validated by Cube on
    query.

    Pass only the measure phrase: breakdowns ("by campaign"), periods and filters
    belong in ``metrics_query``. Live 2026-10-07 the measure plus a breakdown
    resolved to a different metric than the measure alone."""
    try:
        result = await McpSemanticPort(ctx.deps.mcp_client, agent_id=_AGENT_ID).resolve_concept_payload(
            concept, dict(axes or {})
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
            filter_str = f" — bound filter: {filter_bound}. Pass it through in the filters of the query."
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
            + (f" — nearest: {nearest}" if nearest else "") + ". Try catalogue_search_metrics with the user's other words.",
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


async def reject_unknown_metric(ctx: RunContext[SelericDeps], metric_id: str) -> ToolResult | None:
    """Gate an id against the warmed catalogue snapshot before any Cube call.
    Validation only — never rewrites the id to a guess (rule 1 / the
    no-alias-table warning in this module's docstring).

    * id present, or snapshot empty (fail-open) → ``None`` (proceed).
    * absent but close ids exist → a failed result listing them: the model can
      fix it in-context by picking a real id.
    * absent with nothing close → the concept is not modelled. Return a
      non-retryable failure so the model reports it / searches, instead of
      falling through to Cube — whose error is retryable, so the model looped
      guessing more non-existent ids (live L12: inventory_turnover_by_warehouse
      → inventory_on_hand_value_by_warehouse).
    """
    catalogue = ctx.deps.catalogue
    if not catalogue.metrics or catalogue.has_metric(metric_id):
        return None
    candidates = catalogue.closest_metric_ids(metric_id)
    if candidates:
        return ToolResult(
            success=False,
            summary=(
                f"'{metric_id}' is not a catalogue metric id. Closest ids: "
                f"{', '.join(candidates)}. Pick the exact id from the catalogue index and call again."
            ),
            error_code="UNSUPPORTED_QUERY",
            retryable=True,
        )
    # A business word passed as an id ("spend", "revenue") is not an unmodelled
    # concept: the catalogue's own concept resolver names its id. Without this the
    # model was told "not modelled — tell the user it is not available" and printed
    # spend and revenue as "No data available" for every campaign (live 2026-10-08,
    # golden Q9). Resolution only — the id comes from the catalogue, not this code.
    concept = await resolve_concept(ctx, metric_id.replace("_", " "))
    resolved = (concept.provenance.source_metadata or {}).get("metric_id") if concept.success and concept.provenance else None
    if resolved and resolved != metric_id and catalogue.has_metric(resolved):
        return ToolResult(
            success=False,
            summary=(
                f"'{metric_id}' is a business term, not a metric id: the catalogue resolves it to "
                f"'{resolved}' ({concept.summary}). Call again with metric_id='{resolved}'."
            ),
            error_code="UNSUPPORTED_QUERY",
            retryable=True,
        )
    return ToolResult(
        success=False,
        summary=(
            f"'{metric_id}' is not a catalogue metric and no similar metric exists — "
            f"this concept is not modelled. Confirm with catalogue_search_metrics if unsure; "
            f"otherwise tell the user it is not available. Do not guess another id."
        ),
        error_code="UNSUPPORTED_QUERY",
        retryable=False,
    )
