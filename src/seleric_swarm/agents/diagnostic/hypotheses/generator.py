"""Hypothesis generation (constrained).

Seeds, in order:
  1. Observed co-moving metrics (anomalies / evidence). Any registered
     outcome is diagnosable from what actually moved — no per-metric YAML
     story list.
  2. Caller-declared alternatives.
  3. Optional LLM enrichment, bounded to allowed catalogue metric ids
     (observed evidence or metrics mapped onto the causal graph).

Total is capped by ``budgets.max_hypotheses``.
"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel

from seleric_swarm.agents.diagnostic.context import DiagnosticContext
from seleric_swarm.agents.diagnostic.contracts import DiagnosticHypothesis
from seleric_swarm.agents.diagnostic.ontology import graph_id_for_outcome, node_for_metric
from seleric_swarm.services.metrics import MetricRegistry

_log = structlog.get_logger("seleric_swarm.agents.diagnostic")
_METRICS: MetricRegistry | None = None


class _LLMHypo(BaseModel):
    statement: str
    mechanism: str = ""
    treatment_metric: str = ""
    domains: list[str] = []


class _LLMHypoList(BaseModel):
    hypotheses: list[_LLMHypo] = []


async def generate_hypotheses(ctx: DiagnosticContext) -> list[DiagnosticHypothesis]:
    cap = ctx.policies.budget("max_hypotheses")
    outcome = ctx.outcome_metric
    observed = {(e.get("metric_id") or e.get("metric_or_fact")) for e in ctx.evidence}
    observed.update(a.metric_id for a in ctx.anomalies)
    graph = ctx.deps.causal_graphs.get(_graph_id_for(ctx))
    allowed = _allowed_treatment_ids(ctx, observed, graph)

    out: list[DiagnosticHypothesis] = []

    # 0. semantic neighbors from the OM entity cluster (not causal).
    # An ontology-port failure must never break diagnosis — observed
    # co-movers below do not depend on it.
    if ctx.deps.ontology is not None:
        try:
            related = await ctx.deps.ontology.related_metrics(outcome)
            neighbors = list(related.get("related_metrics") or [])
            ctx.scratch["semantic_neighbors"] = neighbors
            ctx.scratch["entity_cluster"] = related.get("entity_cluster")
            ctx.scratch["om_data_product"] = related.get("data_product")
        except Exception as exc:  # noqa: BLE001 - resilience boundary
            _log.debug("diagnostic.hypotheses.ontology_skipped", error=str(exc))

    # 1. observed co-movers — any registered/observed metric can be a treatment
    for hypo in _hypotheses_from_observations(ctx, outcome, already=set()):
        out.append(hypo)

    # 2. explicit alternatives requested by the caller
    for alt in ctx.request.context.get("alternatives_to_test", []) or []:
        out.append(
            DiagnosticHypothesis(
                statement=str(alt),
                mechanism="declared by caller",
                outcome_metric=outcome,
                required_tests=["evidence_sufficiency", "temporal_precedence"],
                synthetic=ctx.synthetic_inputs(),
            )
        )

    # 3. optional LLM enrichment (bounded to allowed catalogue ids)
    if ctx.policies.llm_enrichment() and len(out) < cap:
        try:
            from seleric_swarm.agents.diagnostic.prompts import (
                HYPOTHESIS_SYSTEM,
                hypothesis_user,
            )

            extra = await ctx.deps.reasoning.generate_structured(
                system=HYPOTHESIS_SYSTEM,
                user=hypothesis_user(ctx, existing=out, allowed=sorted(allowed)),
                schema=_LLMHypoList,
                tags=["diagnostic", "hypotheses"],
            )
            for cand in extra.hypotheses:
                tm = cand.treatment_metric.strip()
                if tm and tm in allowed:
                    out.append(
                        DiagnosticHypothesis(
                            statement=cand.statement,
                            mechanism=cand.mechanism,
                            treatment_metric=tm,
                            outcome_metric=outcome,
                            domains=list(cand.domains),
                            required_tests=["temporal_precedence", "segment_specificity"],
                            llm_generated=True,
                            synthetic=ctx.synthetic_inputs(),
                        )
                    )
        except Exception as exc:  # LLM failure must never break diagnosis
            _log.debug("diagnostic.hypotheses.llm_skipped", error=str(exc))

    # De-dupe by canonical mechanism fingerprint (treatment -> outcome), not raw
    # wording — an LLM-phrased hypothesis and an observation-seeded one describing
    # the same driver must collapse to one hypothesis rather than each getting
    # its own id/test plan. Domains are a derived label, not part of what makes
    # two explanations the same mechanism, so they are intentionally excluded.
    seen: set[tuple[str, str]] = set()
    deduped: list[DiagnosticHypothesis] = []
    for h in out:
        tm = h.treatment_metric.strip().lower()
        key = (
            (tm, h.outcome_metric.strip().lower())
            if tm
            else ("__statement__", h.statement.strip().lower())
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(h)
    return deduped[:cap]


def _graph_id_for(ctx: DiagnosticContext) -> str:
    return ctx.request.context.get("graph_id") or graph_id_for_outcome(ctx.outcome_metric)


def _registry(ctx: DiagnosticContext | None = None) -> MetricRegistry:
    if ctx is not None and ctx.deps.metrics is not None:
        return ctx.deps.metrics
    global _METRICS
    if _METRICS is None:
        _METRICS = MetricRegistry("config/metric_registry.yaml")
    return _METRICS


def _allowed_treatment_ids(ctx: DiagnosticContext, observed: set[Any], graph: Any) -> set[str]:
    """Catalogue ids the LLM may name: observed registered metrics, plus
    registered metrics that map onto the active causal graph. Invented ids
    (metric.cosmic_rays) are not in this set.
    """
    registry = _registry(ctx)
    allowed: set[str] = set()
    for raw in observed:
        mid = str(raw or "")
        if mid and _usable_treatment(mid, ctx.outcome_metric) and registry.get(mid) is not None:
            allowed.add(mid)
    if graph is not None:
        nodes = set(graph.nodes)
        for metric in registry.all():
            if not _usable_treatment(metric.id, ctx.outcome_metric):
                continue
            if node_for_metric(metric.id) in nodes:
                allowed.add(metric.id)
    return allowed


def _usable_treatment(metric_id: str, outcome: str) -> bool:
    if not metric_id or metric_id == outcome:
        return False
    if metric_id.startswith("event.") or metric_id.endswith(".delta"):
        return False
    return True


def _label(metric_id: str) -> str:
    return metric_id.removeprefix("metric.").replace("_", " ")


def _domains_for(metric_id: str, ctx: DiagnosticContext) -> list[str]:
    owner = _registry(ctx).owner_agent_for(metric_id)
    if owner:
        return [owner.removesuffix("_agent")]
    for e in ctx.evidence_for_metric(metric_id):
        domain = (e.get("provenance") or {}).get("domain")
        if domain:
            return [str(domain).removesuffix("_agent")]
    return []


def _hypotheses_from_observations(
    ctx: DiagnosticContext, outcome: str, *, already: set[str]
) -> list[DiagnosticHypothesis]:
    """Seed treatments from co-moving anomalies/evidence, not a YAML outcome list."""
    ranked: dict[str, tuple[float, str, list[str]]] = {}
    for a in ctx.anomalies:
        mid = a.metric_id
        if not _usable_treatment(mid, outcome) or mid in already:
            continue
        refs = list(a.evidence_refs or [])
        ranked[mid] = (abs(a.deviation_pct or 0.0), a.direction, refs)
    for e in ctx.evidence:
        mid = str(e.get("metric_id") or e.get("metric_or_fact") or "")
        if not _usable_treatment(mid, outcome) or mid in already:
            continue
        eid = str(e.get("evidence_id") or e.get("artifact_id") or "")
        prev = ranked.get(mid)
        refs = list(prev[2] if prev else [])
        if eid and eid not in refs:
            refs.append(eid)
        try:
            score = abs(float(e.get("change_pct") or 0.0))
        except (TypeError, ValueError):
            score = 0.0
        direction = str((e.get("direction") or (prev[1] if prev else "") or ""))
        ranked[mid] = (max(score, prev[0] if prev else 0.0), direction, refs)

    out: list[DiagnosticHypothesis] = []
    for mid, (_score, direction, refs) in sorted(ranked.items(), key=lambda kv: -kv[1][0]):
        moved = f"moved {direction}" if direction in {"up", "down"} else "moved"
        treat = _label(mid)
        target = _label(outcome)
        out.append(
            DiagnosticHypothesis(
                statement=f"{treat} {moved}, which may have driven {target}.",
                mechanism=f"concurrent movement in {treat} is a candidate driver of {target}",
                treatment_metric=mid,
                outcome_metric=outcome,
                domains=_domains_for(mid, ctx),
                supporting_evidence=refs,
                required_tests=["temporal_precedence", "segment_specificity", "control_divergence"],
                synthetic=ctx.synthetic_inputs(),
            )
        )
    return out
