"""Diagnostic system prompt + prompt builders."""

from __future__ import annotations

from seleric_swarm.agents.diagnostic.context import DiagnosticContext

DIAGNOSTIC_SYSTEM_PROMPT = """\
You are the Diagnostic Agent of the Seleric Intelligence Swarm.

Your job is to propose EXPLICIT, TESTABLE hypotheses for why a metric changed.
You do NOT decide which hypothesis is true, you do NOT estimate effects, and you
do NOT state a root cause. Deterministic tests and a causal engine do that.

Every hypothesis must name:
- a concrete mechanism (one sentence)
- a treatment metric that plausibly drives the outcome
- the business domain(s) that own it

Only propose mechanisms whose treatment metric is already observed in evidence
or present in the causal graph. Do not invent metrics. Prefer specific,
falsifiable mechanisms over vague narratives.
"""

HYPOTHESIS_SYSTEM = DIAGNOSTIC_SYSTEM_PROMPT + """

TASK: given the outcome metric, the observed anomalies and the candidate
treatment metrics, list additional plausible hypotheses NOT already covered by
the deterministic templates. Return each as: statement, mechanism,
treatment_metric, domains.
"""


def hypothesis_user(ctx: DiagnosticContext) -> str:
    observed = sorted({str(e.get("metric_id") or e.get("metric_or_fact")) for e in ctx.evidence})
    anomalies = [
        f"{a.metric_id} {a.deviation_pct:+.1f}% ({a.direction})" if a.deviation_pct is not None
        else f"{a.metric_id} ({a.direction})"
        for a in ctx.anomalies
    ]
    return "\n".join(
        [
            f"Question: {ctx.request.question}",
            f"Outcome metric: {ctx.outcome_metric}",
            f"Degradation started at: {ctx.degradation_started_at}",
            f"Observed metrics: {observed}",
            f"Anomalies: {anomalies}",
            f"Existing template hypotheses: {[h.statement for h in ctx.hypotheses]}",
            f"Max new hypotheses: {max(0, ctx.policies.budget('max_hypotheses') - len(ctx.hypotheses))}",
        ]
    )
