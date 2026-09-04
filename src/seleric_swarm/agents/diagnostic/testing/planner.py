"""Per-hypothesis test planning.

Turns a hypothesis into an ordered list of ``HypothesisTest`` objects. The set is
deterministic: it depends only on the hypothesis shape and what data is
available, never on an LLM.
"""

from __future__ import annotations

from seleric_swarm.agents.diagnostic.context import DiagnosticContext
from seleric_swarm.agents.diagnostic.contracts import DiagnosticHypothesis, HypothesisTest


def plan_tests(ctx: DiagnosticContext, h: DiagnosticHypothesis) -> list[HypothesisTest]:
    cap = ctx.policies.budget("max_tests_per_hypothesis")
    tests: list[HypothesisTest] = []

    tests.append(
        HypothesisTest(
            kind="evidence_sufficiency",
            description="Is there any direct evidence for the treatment metric?",
            hypothesis_id=h.hypothesis_id,
            params={"treatment_metric": h.treatment_metric, "min_supporting": ctx.policies.min_supporting_evidence()},
        )
    )
    if h.treatment_metric:
        tests.append(
            HypothesisTest(
                kind="temporal_precedence",
                description="Did the treatment change before the outcome degraded?",
                hypothesis_id=h.hypothesis_id,
                params={
                    "treatment_metric": h.treatment_metric,
                    "degradation_started_at": ctx.degradation_started_at,
                    "tolerance_minutes": ctx.policies.temporal_tolerance_minutes(),
                },
            )
        )
    # segment specificity + control divergence only when we have a segmented outcome
    if _has_segments(ctx):
        tests.append(
            HypothesisTest(
                kind="segment_specificity",
                description="Is the outcome drop concentrated in the segment the mechanism predicts?",
                hypothesis_id=h.hypothesis_id,
                params={"outcome_metric": ctx.outcome_metric, "min_divergence_pct": ctx.policies.min_segment_divergence_pct()},
            )
        )
        tests.append(
            HypothesisTest(
                kind="control_divergence",
                description="Does an unaffected control segment move much less?",
                hypothesis_id=h.hypothesis_id,
                params={"outcome_metric": ctx.outcome_metric},
            )
        )
    if h.treatment_metric and _has_dose_pairs(ctx, h):
        tests.append(
            HypothesisTest(
                kind="dose_response",
                description="Do units with worse treatment show worse outcome?",
                hypothesis_id=h.hypothesis_id,
                params={"treatment_metric": h.treatment_metric, "min_pairs": ctx.policies.dose_response_min_pairs()},
            )
        )
    tests.append(
        HypothesisTest(
            kind="mechanism_consistency",
            description="Is the mechanism internally consistent with the observed direction of change?",
            hypothesis_id=h.hypothesis_id,
            params={"treatment_metric": h.treatment_metric, "outcome_metric": ctx.outcome_metric},
        )
    )
    return tests[:cap]


def _has_segments(ctx: DiagnosticContext) -> bool:
    return any(
        (e.get("dimensions") or {})
        for e in ctx.evidence
        if (e.get("metric_id") or e.get("metric_or_fact")) == ctx.outcome_metric
    )


def _has_dose_pairs(ctx: DiagnosticContext, h: DiagnosticHypothesis) -> bool:
    tm_rows = [e for e in ctx.evidence if (e.get("metric_id") or e.get("metric_or_fact")) == h.treatment_metric]
    return len(tm_rows) >= 2 or bool(ctx.request.context.get("dose_pairs"))
