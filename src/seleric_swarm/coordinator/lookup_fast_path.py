"""Coordinator-level lookup fast path (Phase 1+2a of retiring lookup_v1).

The legacy lookup_v1 pipeline (orchestration/graph.py + orchestration/
runner.py) tracks "is this metric already fetched" via raw string equality
between metric ids that can legitimately take different forms (bare
catalogue names vs. registry-canonical ``metric.`` ids). That check never
converges when a metric is asked for under both forms, so domain-agent
leadership ping-pongs forever on data it already has (root-caused against
a live "how is attribution doing ... per channel" query that hit
HANDOFF_REJECTED after 4 handoffs despite 44 evidence rows already fetched).

This module sidesteps that bug class structurally instead of just patching
it: every metric is fetched with NO agent-to-agent handoff at all, via one
of these mechanisms per ``DomainQuestion``:

* ungrained lookup -- ``BusinessStateService.get_metric_state`` (canonicalizes
  ids through ``MetricRegistry.get()``), one scalar per metric.
* grained lookup (e.g. "per channel") -- ``DataProvider.fetch(dimensions=...)``
  via ``build_hybrid_bundle()``, reusing the exact dimensioned-breakdown MCP
  query path the old Observer/domain-agent code already proved works
  (``swarm/domain/base.py::observe`` -> ``HybridMcpDataProvider.fetch`` ->
  ``services/mcp_query.py::build_metrics_query_args(dimensions=...)``).
  ``BusinessStateService``/``MetricState`` (Sprints 1-5) are deliberately
  left untouched -- they hold one scalar per metric, not a per-dimension
  breakdown, so this bypasses them rather than bolting breakdown support
  onto their contracts.
* comparison (two periods, e.g. "compare X on date1 and date2") --
  ``BusinessStateService.get_metric_state`` called twice per metric, once
  per period, with a ``.delta`` row computed as ``period_a - period_b``
  (matching ``observer.py::_comparison_deltas``'s convention: positive
  means period A is higher). This was blocked until ``normalize_query()``
  started actually populating ``NormalizedQuery.comparison_range`` (period
  B) instead of always returning ``None`` for it -- see
  ``coordinator/intake/__init__.py``.

Multi-domain lookups (attribution + funnel metrics in one question) are
answered directly in one pass, since there is no leadership to transfer.

Deliberately narrow scope -- returns ``None`` (not an error) to tell the
caller (``orchestration/dispatch.py``) to fall back to the legacy lookup_v1
pipeline for anything outside this scope:

* a query whose classification didn't resolve to any DomainQuestion at all
* comparison intent where the classifier didn't detect a second period
  (``comparison_range`` still ``None``), or where a DomainQuestion also
  carries a dimension ("grain") -- comparing a per-dimension breakdown
  across two periods isn't built.

(Known gap, not fixed here: the classifier doesn't always attach grain for
phrasing like "per channel" -- see docs/features/lookup-v1-retirement.md.
When grain detection misses, this fast path answers with a correct
aggregate rather than the requested breakdown, which is still strictly
better than the pre-fix HANDOFF_REJECTED failure.)

See docs/features/lookup-v1-retirement.md for the retirement plan this
module is part of.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import uuid4

from seleric_swarm.contracts.lookup import EvidenceView, MissionResult, TimeRangeV1, TraceInfo
from seleric_swarm.coordinator.intake import normalize_query
from seleric_swarm.domain.models import MetricState, StateRequest
from seleric_swarm.swarm.providers.mcp_data import build_hybrid_bundle

if TYPE_CHECKING:
    from seleric_swarm.coordinator.contracts import DomainQuestion, NormalizedQuery
    from seleric_swarm.runtime import SwarmRuntime
    from seleric_swarm.swarm.providers.base import MetricReading, ProviderBundle


def _time_range(normalized: NormalizedQuery) -> TimeRangeV1:
    tr = normalized.time_range
    if tr is None or not tr.start:
        return TimeRangeV1(kind="relative", relative_token="last_7d")
    return TimeRangeV1(kind="absolute", start=tr.start, end=tr.end or tr.start)


def _evidence_row(metric_id: str, state: MetricState) -> EvidenceView:
    return EvidenceView(
        evidence_id=f"EV-{uuid4().hex[:12]}",
        metric_or_fact=metric_id,
        value=state.actual,
        time_range=state.window,
        source="deterministic.business_state",
        freshness=state.freshness,
        dimensions=state.dimensions,
        provenance=state.provenance,
    )


def _breakdown_evidence_row(metric_id: str, reading: MetricReading, time_range: TimeRangeV1) -> EvidenceView:
    return EvidenceView(
        evidence_id=f"EV-{uuid4().hex[:12]}",
        metric_or_fact=metric_id,
        value=reading.value,
        unit=reading.unit,
        time_range={"start": time_range.start, "end": time_range.end},
        source=reading.source or "deterministic.mcp_breakdown",
        dimensions=reading.dimensions,
        provenance={"data_origin": reading.data_origin},
    )


def _narrate(evidence: list[EvidenceView]) -> str:
    if not evidence:
        return "No data available for the requested metric(s)."
    by_metric: dict[str, list[EvidenceView]] = {}
    for row in evidence:
        by_metric.setdefault(row.metric_or_fact, []).append(row)
    lines = []
    for metric_id, rows in by_metric.items():
        # Multiple rows for one metric only happens via the dimensioned
        # breakdown fetch (_fetch_breakdown) -- the ungrained path
        # (BusinessStateService) always carries a "brand_id" dimension on
        # its single row too, which must NOT trigger breakdown-style
        # rendering (that showed "20=<value>" instead of "<value>").
        if len(rows) == 1:
            lines.append(f"{metric_id}: {rows[0].value}")
        else:
            breakdown = ", ".join(
                f"{'/'.join(str(v) for v in row.dimensions.values()) or 'total'}={row.value}" for row in rows
            )
            lines.append(f"{metric_id}: {breakdown}")
    return "\n".join(lines)


async def _fetch_breakdown(
    runtime: SwarmRuntime, providers: ProviderBundle, dq: DomainQuestion, time_range: TimeRangeV1
) -> tuple[list[EvidenceView], list[str]]:
    """One DomainQuestion's dimensioned fetch via the same MCP breakdown
    path the old Observer/domain-agent code used -- see module docstring.
    """
    provider = providers.data_for(dq.domain)
    if provider is None:
        return [], [f"{', '.join(dq.metrics)}: no data provider for domain {dq.domain}"]

    result = await provider.fetch(
        metric_ids=dq.metrics,
        time_range={"start": time_range.start, "end": time_range.end},
        dimensions={g: "" for g in dq.grain},
    )
    definitions = {mid: runtime.metrics.get(mid) for mid in dq.metrics}

    def _canon(mid: str) -> str:
        definition = definitions.get(mid)
        return definition.id if definition else mid

    rows = [_breakdown_evidence_row(_canon(reading.metric_id), reading, time_range) for reading in result.readings]
    limitations = [f"{_canon(mid)}: no data available for the requested breakdown" for mid in result.missing]
    return rows, limitations


def _build_providers(runtime: SwarmRuntime) -> ProviderBundle:
    providers, _stats = build_hybrid_bundle(
        mcp=runtime.mcp,
        execution_mode="production",
        metrics=runtime.metrics,
        agents=getattr(runtime, "agents", None),
        bootstrap=getattr(runtime, "bootstrap", None),
        business_state=runtime.business_state,
    )
    return providers


async def _fetch_period_reading(
    providers: ProviderBundle, domain: str, metric_id: str, time_range: TimeRangeV1
) -> MetricReading | None:
    """One period's total via the same aggregating provider fetch the
    ``grained`` branch below already uses (``HybridMcpDataProvider.fetch``) --
    NOT ``BusinessStateService.get_metric_state``, which returns the *last
    daily point* in the range (``state.actual = series[-1].value``), not a
    period total. A single-day range still works fine through this path.
    """
    provider = providers.data_for(domain)
    if provider is None:
        return None
    result = await provider.fetch(
        metric_ids=[metric_id], time_range={"start": time_range.start, "end": time_range.end}
    )
    return result.readings[0] if result.readings else None


def _reading_evidence_row(metric_id: str, reading: MetricReading, time_range: TimeRangeV1) -> EvidenceView:
    return EvidenceView(
        evidence_id=f"EV-{uuid4().hex[:12]}",
        metric_or_fact=metric_id,
        value=reading.value,
        unit=reading.unit,
        time_range={"start": time_range.start, "end": time_range.end},
        source=reading.source or "deterministic.mcp",
        dimensions=reading.dimensions,
        provenance={"data_origin": reading.data_origin},
    )


async def _comparison_rows(
    canonical_id: str,
    reading_a: MetricReading | None,
    time_range_a: TimeRangeV1,
    reading_b: MetricReading | None,
    time_range_b: TimeRangeV1,
) -> tuple[list[EvidenceView], str | None]:
    """Period A / period B rows + one ``.delta`` row, ``period_a - period_b``
    -- matches ``observer.py::_comparison_deltas``'s convention (positive
    means period A, the one named first in the query, is higher).
    """
    if reading_a is None or reading_b is None:
        return [], f"{canonical_id}: no data available for one or both comparison periods"
    row_a = _reading_evidence_row(canonical_id, reading_a, time_range_a)
    row_b = _reading_evidence_row(canonical_id, reading_b, time_range_b)
    delta_value = (
        row_a.value - row_b.value if row_a.value is not None and row_b.value is not None else None
    )
    delta = EvidenceView(
        evidence_id=f"EV-{uuid4().hex[:12]}",
        metric_or_fact=f"{canonical_id}.delta",
        value=delta_value,
        time_range={"start": row_a.time_range.get("start"), "end": row_b.time_range.get("end")},
        source="deterministic.metrics",
        provenance={
            "calculation": "period_a - period_b",
            "period_a_evidence_id": row_a.evidence_id,
            "period_b_evidence_id": row_b.evidence_id,
        },
    )
    return [row_a, row_b, delta], None


async def run_lookup_fast_path(
    runtime: SwarmRuntime,
    *,
    query: str,
    timezone: str = "Asia/Kolkata",
    as_of: str | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
    mission_id: str | None = None,
) -> MissionResult | None:
    """Answer a plain lookup query directly via BusinessStateService, no
    agent handoff. Returns ``None`` to signal the caller to fall back to
    lookup_v1 for anything this fast path doesn't (yet) cover.
    """
    rid = request_id or uuid4().hex
    sid = session_id or uuid4().hex
    mid = mission_id or f"M-{uuid4().hex[:10]}"

    normalized = await normalize_query(
        query,
        timezone=timezone,
        as_of=as_of,
        metrics=runtime.metrics,
        agent_id="coordinator_agent",
        runtime=runtime,
        mission_id=mid,
        request_id=rid,
        session_id=sid,
    )
    if normalized.unsupported_reason or not normalized.domain_questions:
        return None
    intents = set(normalized.intents)

    def _canon(metric_id: str) -> str:
        # Evidence is always keyed by the registry-canonical "metric.xxx" id
        # (matching every other evidence producer in the system), regardless
        # of whether the classifier handed us that form or a bare catalogue
        # name -- MetricRegistry.get() resolves either.
        definition = runtime.metrics.get(metric_id)
        return definition.id if definition else metric_id

    async def _fetch(domain: str, metric_id: str, time_range: TimeRangeV1) -> tuple[str, MetricState]:
        request = StateRequest(
            metric_id=metric_id,
            time_range=time_range,
            agent_id=f"{domain}_agent",
            need=["actual", "features"],
        )
        return _canon(metric_id), await runtime.business_state.get_metric_state(request)

    evidence: list[EvidenceView] = []
    limitations: list[str] = []
    query_class = "lookup"
    narration: str | None = None

    if "comparison" in intents:
        if not normalized.comparison_range or any(dq.grain for dq in normalized.domain_questions):
            return None
        query_class = "comparison"
        time_range_a = _time_range(normalized)
        cr = normalized.comparison_range
        time_range_b = TimeRangeV1(kind="absolute", start=cr.start, end=cr.end or cr.start)
        providers = _build_providers(runtime)
        pairs = [
            (
                _canon(metric_id),
                *await asyncio.gather(
                    _fetch_period_reading(providers, dq.domain, metric_id, time_range_a),
                    _fetch_period_reading(providers, dq.domain, metric_id, time_range_b),
                ),
            )
            for dq in normalized.domain_questions
            for metric_id in dq.metrics
        ]
        comparison_lines = []
        for canonical_id, reading_a, reading_b in pairs:
            rows, limitation = await _comparison_rows(canonical_id, reading_a, time_range_a, reading_b, time_range_b)
            evidence.extend(rows)
            if limitation:
                limitations.append(limitation)
                continue
            delta_row = rows[-1]
            comparison_lines.append(
                f"{canonical_id}: period A={rows[0].value}, period B={rows[1].value}, delta={delta_row.value}"
            )
        narration = "\n".join(comparison_lines) if comparison_lines else None
    else:
        if "lookup" not in intents:
            return None
        time_range = _time_range(normalized)
        ungrained = [dq for dq in normalized.domain_questions if not dq.grain]
        grained = [dq for dq in normalized.domain_questions if dq.grain]

        if ungrained:
            pairs = [(dq.domain, metric_id) for dq in ungrained for metric_id in dq.metrics]
            if time_range.start != time_range.end:
                # Multi-day range ("last 7 days", "this month", ...):
                # BusinessStateService.get_metric_state's `actual` is the
                # LAST day's point in the series, not a period aggregate --
                # correct for "today's value" but silently wrong for a
                # range ask (a week's worth of orders reported as if it were
                # one day's count). Use the same aggregating provider fetch
                # the comparison branch above and the `grained` branch below
                # already rely on.
                providers = _build_providers(runtime)
                readings = await asyncio.gather(
                    *[_fetch_period_reading(providers, domain, metric_id, time_range) for domain, metric_id in pairs]
                )
                for (domain, metric_id), reading in zip(pairs, readings):
                    canonical_id = _canon(metric_id)
                    if reading is None:
                        limitations.append(f"{canonical_id}: no data available")
                        continue
                    evidence.append(_reading_evidence_row(canonical_id, reading, time_range))
            else:
                tasks = [_fetch(domain, metric_id, time_range) for domain, metric_id in pairs]
                for metric_id, state in await asyncio.gather(*tasks):
                    if state.status == "UNAVAILABLE":
                        limitations.append(f"{metric_id}: {', '.join(state.quality_flags) or 'no data available'}")
                        continue
                    evidence.append(_evidence_row(metric_id, state))

        if grained:
            providers = _build_providers(runtime)
            breakdown_tasks = [_fetch_breakdown(runtime, providers, dq, time_range) for dq in grained]
            for rows, missing in await asyncio.gather(*breakdown_tasks):
                evidence.extend(rows)
                limitations.extend(missing)

    status = "completed" if evidence and not limitations else "partial" if evidence else "failed"
    lead_domain = normalized.candidate_domains[0] if normalized.candidate_domains else normalized.domain_questions[0].domain
    lead = f"{lead_domain}_agent"

    result = MissionResult(
        mission_id=mid,
        status=status,  # type: ignore[arg-type]
        query_class=query_class,
        mission_lead=lead,
        initial_mission_lead=lead,
        evidence=evidence,
        limitations=limitations,
        final_response=narration if narration is not None else _narrate(evidence),
        trace=TraceInfo(request_id=rid, session_id=sid),
    )
    try:
        runtime.store.put(
            result,
            {
                "route": "lookup",
                "workflow": "lookup_fast_path",
                "user_query": query,
                **result.model_dump(),
            },
        )
    except Exception:  # noqa: S110 - persistence must never fail a completed mission
        pass
    return result
