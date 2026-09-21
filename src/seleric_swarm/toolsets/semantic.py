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

import json
from datetime import datetime
from typing import Any

from pydantic_ai import ModelRetry, RunContext

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.services.mcp_query import (
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


def _sanitize_dimensions(dimensions: dict[str, str] | None) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, raw in (dimensions or {}).items():
        if raw is None:
            cleaned[str(key)] = ""
            continue
        text = str(raw).strip()
        if text and _is_self_referential_dimension_value(key, text):
            cleaned[str(key)] = ""
            continue
        if text and _is_placeholder_dimension_value(text):
            continue
        cleaned[str(key)] = text
    return cleaned


def _unknown_dimension_error(error: object) -> bool:
    text = str(error).lower()
    return "unknown brand" in text or "unknown dimension" in text or "invalid brand" in text


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


async def _cached_metrics_query(
    ctx: RunContext[SelericDeps], arguments: dict[str, Any]
) -> dict[str, Any]:
    fetch = lambda: call_metrics_query(ctx.deps.mcp_client, agent_id=_AGENT_ID, arguments=arguments)  # noqa: E731
    if not _QUERY_CACHE_ENABLED:
        return await fetch()
    key = _cache_key("seleric.metrics_query", arguments)
    return await ctx.deps.query_cache.get_or_fetch(key, fetch)


def _mcp_error_result(exc: Exception) -> ToolResult:
    return ToolResult(
        success=False,
        summary=f"{type(exc).__name__}: {exc}",
        error_code="MCP_UNAVAILABLE",
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


async def search_semantics(ctx: RunContext[SelericDeps], query: str) -> ToolResult:
    """Resolve business language to catalogue metric ids via the local Qdrant
    catalogue index (``toolsets/catalogue_index.py``), kept in sync with the
    live catalogue by ``scripts/sync_catalogue_to_qdrant.py``. This replaces
    the former ``catalogue_search_metrics``/``catalogue_resolve_term`` MCP
    round trips — search only; ``get_metric_definition``/``query_metrics``
    still validate against the live catalogue/Cube unchanged (rule 1)."""
    try:
        matches = catalogue_index.search(query, kind="metric")
    except Exception as exc:  # convert to ToolResult, never raise across the tool boundary
        return _mcp_error_result(exc)
    warnings = [] if matches else [f"no catalogue match for '{query}'"]
    if any(match.get("stale") for match in matches):
        warnings.append("catalogue index may be stale; rerun scripts/sync_catalogue_to_qdrant.py")
    return ToolResult(
        success=True,
        summary=f"{len(matches)} metric(s) matched '{query}'",
        warnings=warnings,
        provenance=ArtifactProvenance(source_metadata={"matches": matches}),
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


async def get_metric_definition(ctx: RunContext[SelericDeps], metric_id: str) -> ToolResult:
    """Fetch one metric's full catalogue definition (catalogue_get_metric)."""
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


async def query_metrics(
    ctx: RunContext[SelericDeps],
    metric_id: str,
    dimensions: dict[str, str] | None = None,
    grain: str = "none",
    period_start: datetime | None = None,
    period_end: datetime | None = None,
) -> ToolResult:
    """The only path to a numeric metric value. Writes one EvidenceArtifact.

    ``dimensions`` / periods default empty-or-as_of so the model can look up
    "gross sale" without inventing a brand filter or a training-data year.
    """
    _reject_unknown_metric(ctx, metric_id)
    dimensions = _sanitize_dimensions(dimensions)
    period_end = period_end or ctx.deps.as_of
    period_start = period_start or period_end
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
    result = await _cached_metrics_query(ctx, args)
    if result.get("error") and filters and _unknown_dimension_error(result["error"]):
        dimensions = {}
        breakdown = []
        filters = []
        args = build_metrics_query_args(
            measure=metric_id,
            start=period_start.date().isoformat(),
            end=period_end.date().isoformat(),
            grain=None if grain == "none" else grain,
        )
        result = await _cached_metrics_query(ctx, args)
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
        artifact_ids: list[str] = []
        last_value: float | None = None
        for row, bucket_date in zip(rows, per_row_dates, strict=True):
            value = row.get(metric_id)
            if value is None:
                continue
            last_value = float(value)
            bucket_start = datetime.fromisoformat(bucket_date).replace(tzinfo=period_start.tzinfo) if bucket_date else period_start
            bucket_end = bucket_start if bucket_date else period_end
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
        if not artifact_ids:
            return ToolResult(
                success=False,
                summary=f"no usable value for {metric_id} over {period_start.date()}..{period_end.date()}",
                error_code="INSUFFICIENT_EVIDENCE",
                retryable=False,
            )
        return ToolResult(
            success=True,
            artifact_ids=artifact_ids,
            summary=f"{metric_id}={last_value} over {period_start.date()}..{period_end.date()} ({len(artifact_ids)} row(s))",
            provenance=provenance,
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
    return await ctx.deps.query_cache.get_or_fetch(_cache_key("query_metrics_result", args), _write_evidence)


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
        return ToolResult(
            success=False,
            summary=f"drilldown({metric_id}) parent query failed: {parent.get('error') or 'no query_id'}",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=True,
        )
    drilldown_args = {"parent_query_id": parent["query_id"], "target_dimensions": [dimension]}
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
