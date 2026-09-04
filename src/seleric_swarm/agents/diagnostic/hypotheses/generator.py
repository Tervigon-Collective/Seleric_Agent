"""Hypothesis generation (constrained).

Deterministic template hypotheses from the ontology are ALWAYS produced. The
reasoning model may add extra candidates, but only ones whose treatment metric is
already present in evidence or in the causal graph for the outcome -- it cannot
invent free-form causes. Total is capped by ``budgets.max_hypotheses``.
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel

from seleric_swarm.agents.diagnostic.context import DiagnosticContext
from seleric_swarm.agents.diagnostic.contracts import DiagnosticHypothesis
from seleric_swarm.agents.diagnostic.ontology import mechanisms_for

_log = structlog.get_logger("seleric_swarm.agents.diagnostic")


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
    graph = ctx.deps.causal_graphs.get(_graph_id_for(ctx))
    graph_nodes = set(graph.nodes) if graph else set()

    out: list[DiagnosticHypothesis] = []

    # 0. semantic neighbors from the OM entity cluster (not causal)
    if ctx.deps.ontology is not None:
        related = await ctx.deps.ontology.related_metrics(outcome)
        neighbors = list(related.get("related_metrics") or [])
        ctx.scratch["semantic_neighbors"] = neighbors
        ctx.scratch["entity_cluster"] = related.get("entity_cluster")
        ctx.scratch["om_data_product"] = related.get("data_product")

    # 1. deterministic template hypotheses
    for tpl in mechanisms_for(outcome):
        support = [
            e["evidence_id"] if "evidence_id" in e else e.get("artifact_id", "")
            for e in ctx.evidence
            if (e.get("metric_id") or e.get("metric_or_fact")) in set(tpl.evidence_hints)
        ]
        out.append(
            DiagnosticHypothesis(
                statement=tpl.statement,
                mechanism=tpl.mechanism,
                treatment_metric=tpl.treatment_metric,
                outcome_metric=outcome,
                domains=list(tpl.domains),
                supporting_evidence=[s for s in support if s],
                required_tests=["temporal_precedence", "segment_specificity", "control_divergence"],
                synthetic=ctx.synthetic_inputs(),
            )
        )

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

    # 3. optional LLM enrichment (bounded to known metrics)
    if ctx.policies.llm_enrichment() and len(out) < cap:
        try:
            from seleric_swarm.agents.diagnostic.prompts import (
                HYPOTHESIS_SYSTEM,
                hypothesis_user,
            )

            extra = await ctx.deps.reasoning.generate_structured(
                system=HYPOTHESIS_SYSTEM,
                user=hypothesis_user(ctx),
                schema=_LLMHypoList,
                tags=["diagnostic", "hypotheses"],
            )
            for cand in extra.hypotheses:
                tm = cand.treatment_metric.strip()
                if tm and (tm in observed or tm in graph_nodes):
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

    # de-dupe by statement, keep first, cap
    seen: set[str] = set()
    deduped: list[DiagnosticHypothesis] = []
    for h in out:
        key = h.statement.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(h)
    return deduped[:cap]


def _graph_id_for(ctx: DiagnosticContext) -> str:
    return (
        ctx.request.context.get("graph_id")
        or {
            "metric.purchase_cvr": "causal.funnel_purchase.v1",
            "metric.cac": "causal.funnel_purchase.v1",
            "metric.net_sales": "causal.funnel_purchase.v1",
        }.get(ctx.outcome_metric, "")
    )
