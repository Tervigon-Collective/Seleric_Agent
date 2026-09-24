"""AdsToolset — read-only ad-platform surfaces over the live ``seleric-mcp``
gateway. No campaign/adset/ad writes are wired here by design.

Two provenance tiers, deliberately kept distinct:

- ``query_meta_insights`` is **Cube-backed** (the gateway's ``meta_insights_query``
  runs the same QueryPlanner over certified ``meta_ad_performance`` measures as
  ``metrics_query``). Its numbers are semantic-layer-certified, so it writes
  immutable ``EvidenceArtifact``s exactly like ``semantic.query_metrics`` — one
  per (row × measure) — and the model reports the exact stored values.
- ``list_meta_accounts`` / ``list_google_accounts`` / ``query_google_ads`` hit
  the **live** Graph / GAQL APIs. Those rows are discovery/reference data that
  never passed through the Cube semantic layer, freshness gate, or metric
  catalogue, so they are returned in ``provenance.source_metadata`` (no
  EvidenceArtifact) with a warning marking them live and uncertified — the model
  must not present them as certified metric evidence.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta
from typing import Any

from pydantic_ai import RunContext

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.services.mcp_query import dimension_value, row_date

# Single agent identity for MCPGateway allowlisting (matches semantic/actions).
_AGENT_ID = "v3_agent"

_MAX_SERIES_IN_SUMMARY = 40


def _mcp_error(exc: Exception) -> ToolResult:
    return ToolResult(
        success=False,
        summary=f"{type(exc).__name__}: {exc}",
        error_code="MCP_UNAVAILABLE",
        retryable=True,
    )


def _bucket_end(bucket_start: datetime, grain: str) -> datetime:
    """Inclusive end of one Cube time bucket (day → same day)."""
    if grain == "week":
        return bucket_start + timedelta(days=6)
    if grain == "month":
        last = calendar.monthrange(bucket_start.year, bucket_start.month)[1]
        return bucket_start.replace(day=last)
    return bucket_start


def _is_dimension_key(key: Any) -> bool:
    """A row key is a real dimension (kept) unless it's the granularity date
    member (``<view>.<dim>.<granularity>``), which is handled separately."""
    return str(key).rsplit(".", 1)[-1] not in {
        "second", "minute", "hour", "day", "week", "month", "quarter", "year",
    }


async def query_meta_insights(
    ctx: RunContext[SelericDeps],
    account_id: str,
    fields: list[str],
    period_start: datetime,
    period_end: datetime,
    level: str = "account",
    grain: str = "none",
    limit: int | None = None,
) -> ToolResult:
    """Meta ad performance from the **certified Cube semantic layer** (not the
    Graph Insights API). Writes one EvidenceArtifact per (row × measure).

    ``level`` is account | campaign | adset | ad. ``fields`` are insight names
    (spend, impressions, clicks, ctr, cpc, cpm, reach, frequency, link_clicks,
    landing_page_views, thruplays, actions, action_values). ``grain`` is
    day | week | month | none. ``account_id`` is the Meta ad account id — get it
    from ``list_meta_accounts`` when unknown. Breakdowns are not supported on
    this path; use ``level`` for the entity grain.
    """
    if not fields:
        return ToolResult(
            success=False,
            summary="query_meta_insights needs at least one field (e.g. spend, ctr)",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
        )
    time_range = {"since": period_start.date().isoformat(), "until": period_end.date().isoformat()}
    args: dict[str, Any] = {
        "account_id": account_id,
        "level": level,
        "fields": list(fields),
        "time_range": time_range,
        "granularity": grain,
    }
    if limit is not None:
        args["limit"] = limit
    try:
        result = await ctx.deps.mcp_client.call(
            agent_id=_AGENT_ID, capability="seleric.meta_insights_query", arguments=args
        )
    except Exception as exc:
        return _mcp_error(exc)
    result = dict(result or {})
    if result.get("error"):
        return ToolResult(
            success=False,
            summary=f"query_meta_insights failed: {result['error']}",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=True,
            provenance=ArtifactProvenance(source_metadata=result),
        )
    rows = result.get("rows") or []
    if not rows:
        return ToolResult(
            success=False,
            summary=f"no Meta insights for account {account_id} over {period_start.date()}..{period_end.date()}",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
        )
    provenance = result.get("provenance") or {}
    insight_ctx = result.get("insight_context") or {}
    # The Cube measures the fields mapped to (rows are keyed by these). Fall back
    # to the field names if the gateway didn't echo the mapping.
    measures = insight_ctx.get("measures") or list(fields)
    measure_set = set(measures)
    currency = str(provenance.get("currency") or "").strip() or None

    artifact_ids: list[str] = []
    series: list[dict[str, Any]] = []
    for row in rows:
        bucket_date = row_date(row) if grain != "none" else None
        bucket_start = (
            datetime.fromisoformat(bucket_date).replace(tzinfo=period_start.tzinfo)
            if bucket_date
            else period_start
        )
        bucket_end = _bucket_end(bucket_start, grain) if bucket_date else period_end
        # Dimensions = every non-measure, non-date field on the row (the level's
        # id/name), leaf-named so a campaign/adset breakdown is distinguishable.
        row_dims = {
            str(k).split(".")[-1]: str(v)
            for k, v in row.items()
            if k not in measure_set and (not bucket_date or v != bucket_date) and _is_dimension_key(k)
        }
        for measure in measures:
            value = dimension_value(row, measure)
            if value is None:
                continue
            evidence = EvidenceArtifact(
                metric_id=str(measure),
                dimensions={**row_dims, "level": level, "account_id": account_id},
                grain=grain,  # type: ignore[arg-type]
                as_of=ctx.deps.as_of,
                period_start=bucket_start,
                period_end=bucket_end,
                value=float(value),
                unit=currency,
                source_query=args,
            )
            artifact = ctx.deps.artifact_store.put(
                Artifact(
                    workspace_id=ctx.deps.principal.workspace_id,
                    artifact_type="evidence",
                    payload=evidence.model_dump(mode="json"),
                    classification="factual",
                    evidence_ids=[
                        f"meta:{measure}:{account_id}:{bucket_start.date()}:"
                        + ",".join(sorted(row_dims.values()))
                    ],
                    provenance=ArtifactProvenance(source_metadata=provenance),
                    mission_id=ctx.deps.mission_id,
                )
            )
            artifact_ids.append(artifact.id)
            label_bits = [f"{k}={v}" for k, v in row_dims.items()]
            if bucket_date:
                label_bits.insert(0, bucket_date)
            label = ", ".join(label_bits) if label_bits else f"{period_start.date()}..{period_end.date()}"
            series.append({"label": f"{label} · {measure}", "value": float(value)})
    if not artifact_ids:
        return ToolResult(
            success=False,
            summary=f"Meta insights for {account_id} had no usable numeric values",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
        )
    shown = series[:_MAX_SERIES_IN_SUMMARY]
    body = "; ".join(f"{s['label']}={s['value']}" for s in shown)
    more = "" if len(series) <= _MAX_SERIES_IN_SUMMARY else f"; …(+{len(series) - _MAX_SERIES_IN_SUMMARY} more in evidence)"
    summary = (
        f"Meta insights ({level}) for {account_id} over "
        f"{period_start.date()}..{period_end.date()} — use these exact values: {body}{more}"
    )
    ctx.deps.scratchpad.note(summary)
    return ToolResult(
        success=True,
        artifact_ids=artifact_ids,
        summary=summary,
        provenance=ArtifactProvenance(source_metadata={**provenance, "series": series}),
        warnings=list(result.get("warnings") or []),
    )


_LIVE_UNCERTIFIED = (
    "Live ad-platform data (outside the Cube semantic layer): uncertified, not "
    "freshness-gated. Present as a live figure, not a certified metric."
)


async def list_meta_accounts(
    ctx: RunContext[SelericDeps],
    brand_id: str | None = None,
    account_id: str | None = None,
    limit: int = 100,
) -> ToolResult:
    """List accessible Meta ad accounts (live GET /me/adaccounts) to discover an
    ``account_id`` for ``query_meta_insights``. Select the tenant credential with
    ``brand_id`` or ``account_id``. Reference data — not certified evidence."""
    args: dict[str, Any] = {"limit": limit}
    if brand_id:
        args["brand_id"] = brand_id
    if account_id:
        args["account_id"] = account_id
    return await _live_reference(ctx, "seleric.meta_accounts_list", args, "Meta ad accounts")


async def list_google_accounts(
    ctx: RunContext[SelericDeps],
    brand_id: str | None = None,
    customer_id: str | None = None,
) -> ToolResult:
    """List accessible Google Ads customer IDs (live ListAccessibleCustomers) to
    discover a ``customer_id`` for ``query_google_ads``. Reference data."""
    args: dict[str, Any] = {}
    if brand_id:
        args["brand_id"] = brand_id
    if customer_id:
        args["customer_id"] = customer_id
    return await _live_reference(
        ctx, "seleric.google_accounts_list_accessible", args, "Google Ads accounts"
    )


async def query_google_ads(
    ctx: RunContext[SelericDeps],
    customer_id: str,
    query: str,
    page_size: int = 1000,
    brand_id: str | None = None,
) -> ToolResult:
    """Run a read-only SELECT GAQL query against a Google Ads customer (live
    GoogleAdsService.Search — only SELECT is permitted). Returns the rows for the
    model to interpret. LIVE and uncertified — not Cube-backed evidence; do not
    present GAQL numbers as certified metrics."""
    args: dict[str, Any] = {"customer_id": customer_id, "query": query, "page_size": page_size}
    if brand_id:
        args["brand_id"] = brand_id
    return await _live_reference(ctx, "seleric.google_query_gaql", args, "GAQL rows", flag=True)


async def _live_reference(
    ctx: RunContext[SelericDeps],
    capability: str,
    arguments: dict[str, Any],
    label: str,
    *,
    flag: bool = False,
) -> ToolResult:
    """Shared caller for the live read-only tools: return rows in
    ``source_metadata`` (no EvidenceArtifact), with an uncertified caveat when
    the payload carries numbers the model might otherwise cite as a metric."""
    try:
        result = await ctx.deps.mcp_client.call(
            agent_id=_AGENT_ID, capability=capability, arguments=arguments
        )
    except Exception as exc:
        return _mcp_error(exc)
    result = dict(result or {})
    if result.get("error"):
        return ToolResult(
            success=False,
            summary=str(result["error"]),
            error_code="MCP_UNAVAILABLE" if result.get("code") == "UNAVAILABLE" else "ACTION_REJECTED",
            retryable=False,
            provenance=ArtifactProvenance(source_metadata=result),
        )
    data = result.get("data")
    count = len(data) if isinstance(data, list) else (1 if data else 0)
    warnings = [_LIVE_UNCERTIFIED] if flag else []
    return ToolResult(
        success=True,
        summary=f"{label}: {count} row(s)",
        warnings=warnings,
        provenance=ArtifactProvenance(source_metadata=result),
    )
