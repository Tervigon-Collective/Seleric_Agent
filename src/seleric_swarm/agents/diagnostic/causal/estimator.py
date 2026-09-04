"""Causal estimation for the top surviving hypothesis.

Builds a ``CausalEstimationQuery`` from the hypothesis + the causal graph, calls
the injected ``CausalEstimationService`` (template or DoWhy-backed), then derives
a confidence tier. Temporal ordering is enforced here too -- an outcome that
strictly precedes the treatment yields ``REJECTED`` no matter what the estimator
returns.
"""

from __future__ import annotations

from datetime import UTC, datetime

from seleric_swarm.agents.diagnostic.context import DiagnosticContext
from seleric_swarm.agents.diagnostic.contracts import (
    CausalAnalysisArtifact,
    CausalConfidence,
    DiagnosticHypothesis,
)
from seleric_swarm.agents.diagnostic.registries import CausalEstimationQuery


async def estimate_for_hypothesis(
    ctx: DiagnosticContext, h: DiagnosticHypothesis
) -> tuple[CausalAnalysisArtifact, CausalConfidence]:
    graph_id = _graph_id(ctx)
    graph = ctx.deps.causal_graphs.get(graph_id) if graph_id else None
    common_causes = _common_causes(ctx, h, graph)

    treatment_at = _first_treatment_time(ctx, h)
    outcome_at = ctx.degradation_started_at

    query = CausalEstimationQuery(
        treatment=h.treatment_metric or _fallback_treatment(h),
        outcome=ctx.outcome_metric,
        common_causes=common_causes,
        graph_id=graph_id,
        estimator=ctx.policies.estimator(),
        refuters=ctx.policies.refuters(),
        treatment_started_at=treatment_at,
        outcome_started_at=outcome_at,
        mission_id=ctx.request.mission_id,
        hypothesis_id=h.hypothesis_id,
    )
    artifact = await ctx.deps.causal_service.estimate(query, observations=ctx.request.observations)

    confidence = _confidence(ctx, artifact, graph, treatment_at, outcome_at)
    # Metadata-only estimates are capped unless the caller explicitly trusts the
    # declared causal truth (fixture / replay mode). A real Coordinator call with
    # no observation frame stays capped -> the finding is 'inconclusive', which is
    # the honest outcome.
    if ctx.request.observations is None and not ctx.request.context.get("trust_metadata_causal"):
        confidence = ctx.policies.cap_metadata_confidence(confidence)  # type: ignore[assignment]
    return artifact, confidence  # type: ignore[return-value]


def _confidence(
    ctx: DiagnosticContext,
    art: CausalAnalysisArtifact,
    graph,
    treatment_at: str | None,
    outcome_at: str | None,
) -> str:
    # temporal gate
    if ctx.policies.causal_flag("require_temporal_check") and treatment_at and outcome_at:
        t, o = _parse(treatment_at), _parse(outcome_at)
        if t and o and t > o:
            return "REJECTED"

    graph_ok = True
    if ctx.policies.causal_flag("require_graph"):
        if not art.graph_id or graph is None:
            graph_ok = False
        elif art.treatment and art.outcome:
            t_node, o_node = _node(art.treatment), _node(art.outcome)
            if t_node in graph.nodes and o_node in graph.nodes and not graph.has_path(t_node, o_node):
                graph_ok = False

    passed = sum(1 for r in art.refutation_results if r.get("passed"))
    total = len(art.refutation_results)
    need = ctx.policies.causal_min_refutations()

    if not art.passed or not graph_ok:
        return "PLAUSIBLE_CAUSAL" if total else "ASSOCIATION_ONLY"
    if total >= need and passed == total and len(art.common_causes) >= 1:
        return "STRONGLY_SUPPORTED"
    if total >= 1 and passed == total:
        return "CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS"
    return "PLAUSIBLE_CAUSAL"


def _graph_id(ctx: DiagnosticContext) -> str:
    return ctx.request.context.get("graph_id") or "causal.funnel_purchase.v1"


def _common_causes(ctx: DiagnosticContext, h: DiagnosticHypothesis, graph) -> list[str]:
    declared = ctx.request.context.get("common_causes")
    if declared:
        return list(declared)
    base = ["metric.sessions", "campaign", "device"]
    if ctx.outcome_metric == "metric.cac":
        base.append("metric.return_rate")
    return base


def _first_treatment_time(ctx: DiagnosticContext, h: DiagnosticHypothesis) -> str | None:
    from seleric_swarm.agents.diagnostic.testing.runners import _TREATMENT_EVENTS

    for ev_key in _TREATMENT_EVENTS.get(h.treatment_metric, ()):
        if ev_key in ctx.event_times:
            return ctx.event_times[ev_key]
    for e in ctx.evidence_for_metric(h.treatment_metric):
        ts = e.get("start_time") or (e.get("time_range") or {}).get("start")
        if ts:
            return str(ts)
    return ctx.request.context.get("treatment_started_at")


def _fallback_treatment(h: DiagnosticHypothesis) -> str:
    return h.treatment_metric or "unknown_treatment"


def _node(metric_id: str) -> str:
    return {
        "metric.mobile_lcp_seconds": "page_latency",
        "metric.js_error_rate": "page_latency",
        "metric.purchase_cvr": "purchase",
        "metric.cac": "purchase",
        "metric.avg_price": "price",
        "metric.in_stock_rate": "stock",
        "metric.payment_failure_rate": "payment_failure",
    }.get(metric_id, metric_id)


def _parse(value: str) -> datetime | None:
    v = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
