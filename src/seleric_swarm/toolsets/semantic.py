"""SemanticToolset v0 — the only path to numeric business data (rule 5).

Thin wrappers over live ``seleric-mcp`` catalogue/metrics tools via the
already-live ``MCPGateway`` (``protocols/mcp/gateway.py``) and its generic
arg-builder (``services/mcp_query.py``) — both reused as-is, not rebuilt.

No local metric registry, no keyword/alias resolution: ``metric_id`` is
whatever the LLM states, validated by the live catalogue itself (rule 1).
This is deliberately NOT ``services/measure.py::resolve_measure`` — that
module's keyword-overlap matching is exactly the heuristic layer this
profile retires (``docs/BUG_SHEET.md`` bug #8), not something to carry
forward into the new toolset.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic_ai import RunContext

from seleric_swarm.agent.contracts import EvidenceArtifact, ToolResult
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.services.mcp_query import build_metrics_query_args, call_metrics_query, dimension_value

if TYPE_CHECKING:
    from seleric_swarm.agent.contracts import SelericDeps

# Single agent identity for MCPGateway allowlisting/module-pin lookup — the
# new runtime has one agent loop, not per-domain agent ids (see
# docs/refactor/01_PROFILE_RUNTIME.md: no handoff concept in the new loop).
# Reuses "observer_agent" (config/agent_registry.yaml already unions every
# domain agent's seleric capabilities onto it) rather than adding a new
# entry to a registry this migration retires (AgentRegistry/agent_registry.yaml
# per-specialist flags go away once toolset registration replaces it,
# docs/refactor/01_PROFILE_RUNTIME.md "Retires"). Revisit once Profile A's
# real toolset-registration replacement lands.
_AGENT_ID = "observer_agent"


def _mcp_error_result(exc: Exception) -> ToolResult:
    return ToolResult(
        success=False,
        summary=f"{type(exc).__name__}: {exc}",
        error_code="MCP_UNAVAILABLE",
        retryable=True,
    )


async def search_semantics(ctx: RunContext[SelericDeps], query: str) -> ToolResult:
    """Resolve business language to catalogue metric ids (catalogue_search_metrics)."""
    try:
        result = await ctx.deps.mcp_client.call(
            agent_id=_AGENT_ID, capability="seleric.catalogue_search_metrics", arguments={"query": query}
        )
    except Exception as exc:  # noqa: BLE001 - convert to ToolResult, never raise across the tool boundary
        return _mcp_error_result(exc)
    matches = (result or {}).get("matches") or []
    return ToolResult(
        success=True,
        summary=f"{len(matches)} metric(s) matched '{query}'",
        warnings=[] if matches else [f"no catalogue match for '{query}'"],
        provenance=ArtifactProvenance(source_metadata={"matches": matches}),
    )


async def get_metric_definition(ctx: RunContext[SelericDeps], metric_id: str) -> ToolResult:
    """Fetch one metric's full catalogue definition (catalogue_get_metric)."""
    try:
        result = await ctx.deps.mcp_client.call(
            agent_id=_AGENT_ID, capability="seleric.catalogue_get_metric", arguments={"metric_id": metric_id}
        )
    except Exception as exc:  # noqa: BLE001
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


async def query_metrics(
    ctx: RunContext[SelericDeps],
    metric_id: str,
    dimensions: dict[str, str],
    grain: str,
    period_start: datetime,
    period_end: datetime,
) -> ToolResult:
    """The only path to a numeric metric value. Writes one EvidenceArtifact."""
    breakdown = [k for k, v in dimensions.items() if not v]
    filters = [
        {"dimension": k, "operator": "equals", "values": [v]} for k, v in dimensions.items() if v
    ]
    args = build_metrics_query_args(
        measure=metric_id,
        start=period_start.date().isoformat(),
        end=period_end.date().isoformat(),
        grain=None if grain == "none" else grain,
        dimensions=breakdown or None,
        filters=filters or None,
    )
    result = await call_metrics_query(ctx.deps.mcp_client, agent_id=_AGENT_ID, arguments=args)
    if result.get("error"):
        return ToolResult(
            success=False,
            summary=f"query_metrics({metric_id}) failed: {result['error']}",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=True,
        )
    rows = result.get("rows") or []
    if not rows:
        return ToolResult(
            success=False,
            summary=f"no data for {metric_id} over {period_start.date()}..{period_end.date()}",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
        )
    row = rows[0]
    value = row.get(metric_id)
    provenance = ArtifactProvenance(
        query_version=str(result.get("provenance", {}).get("query_id") or ""),
        source_metadata=result.get("provenance") or {},
    )
    evidence = EvidenceArtifact(
        metric_id=metric_id,
        dimensions={k: v for k, v in dimensions.items() if v},
        grain=grain,  # type: ignore[arg-type]
        as_of=ctx.deps.as_of,
        period_start=period_start,
        period_end=period_end,
        value=float(value) if value is not None else None,
        source_query=args,
    )
    artifact = ctx.deps.artifact_store.put(
        Artifact(
            workspace_id=ctx.deps.principal.workspace_id,
            artifact_type="evidence",
            payload=evidence.model_dump(mode="json"),
            classification="factual",
            evidence_ids=[f"raw:{metric_id}:{period_start.date()}:{period_end.date()}"],
            provenance=provenance,
            mission_id=ctx.deps.mission_id,
        )
    )
    return ToolResult(
        success=True,
        artifact_ids=[artifact.id],
        summary=f"{metric_id}={evidence.value} over {period_start.date()}..{period_end.date()}",
        provenance=provenance,
        warnings=list(result.get("warnings") or []),
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
    parent = await call_metrics_query(ctx.deps.mcp_client, agent_id=_AGENT_ID, arguments=parent_args)
    if parent.get("error") or not parent.get("query_id"):
        return ToolResult(
            success=False,
            summary=f"drilldown({metric_id}) parent query failed: {parent.get('error') or 'no query_id'}",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=True,
        )
    try:
        result: dict[str, Any] = await ctx.deps.mcp_client.call(
            agent_id=_AGENT_ID,
            capability="seleric.metrics_drilldown",
            arguments={"parent_query_id": parent["query_id"], "target_dimensions": [dimension]},
        )
    except Exception as exc:  # noqa: BLE001
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
