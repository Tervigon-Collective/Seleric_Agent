"""Diagnostic system prompt + prompt builders."""

from __future__ import annotations

from typing import Any

from seleric_swarm.agents.diagnostic.context import DiagnosticContext
from seleric_swarm.agents.diagnostic.contracts import DiagnosticHypothesis

DIAGNOSTIC_SYSTEM_PROMPT = """\
You are the Diagnostic Agent of the Seleric Intelligence Swarm.

Your job is to propose EXPLICIT, TESTABLE hypotheses for why a metric changed.
You do NOT decide which hypothesis is true, you do NOT estimate effects, and you
do NOT state a root cause. Deterministic tests and a causal engine do that.

Every hypothesis must name:
- a concrete mechanism (one sentence)
- a treatment metric that plausibly drives the outcome
- the business domain(s) that own it

Only propose mechanisms whose treatment_metric is in the allowed catalogue id
list. Do not invent metrics. Prefer specific, falsifiable mechanisms over
vague narratives.
"""

HYPOTHESIS_SYSTEM = DIAGNOSTIC_SYSTEM_PROMPT + """

TASK: given the outcome metric, the observed anomalies, the evidence rows, and
the allowed catalogue metric ids, list additional plausible hypotheses NOT
already covered by the observation-seeded candidates. Return each as:
statement, mechanism, treatment_metric, domains.
"""


def _safe(value: Any, *, max_len: int = 80) -> str:
    text = " ".join(str(value).split())
    if len(text) > max_len:
        return text[:max_len] + "…"
    return text


def _evidence_line(row: dict[str, Any]) -> str:
    mid = row.get("metric_id") or row.get("metric_or_fact") or ""
    parts = [f"id={_safe(mid)}"]
    if row.get("value") is not None:
        parts.append(f"value={_safe(row['value'])}")
    if row.get("change_pct") is not None:
        parts.append(f"change_pct={_safe(row['change_pct'])}")
    dims = row.get("dimensions") or {}
    if dims:
        parts.append(f"segments={_safe(dims)}")
    ts = row.get("start_time") or (row.get("time_range") or {}).get("start")
    if ts:
        parts.append(f"timestamp={_safe(ts)}")
    return "; ".join(parts)


def hypothesis_user(
    ctx: DiagnosticContext,
    *,
    existing: list[DiagnosticHypothesis] | None = None,
    allowed: list[str] | None = None,
) -> str:
    existing = existing if existing is not None else ctx.hypotheses
    allowed_ids = list(allowed or [])
    observed_ids = {str(e.get("metric_id") or e.get("metric_or_fact") or "") for e in ctx.evidence}
    ranked = [m for m in allowed_ids if m in observed_ids] + [m for m in allowed_ids if m not in observed_ids]
    anomalies = [
        f"{a.metric_id} {a.deviation_pct:+.1f}% ({a.direction})" if a.deviation_pct is not None
        else f"{a.metric_id} ({a.direction})"
        for a in ctx.anomalies
    ]
    evidence_lines = [_evidence_line(e) for e in ctx.evidence[:40]]
    anomaly_block = [f"- {line}" for line in anomalies] or ["- (none)"]
    evidence_block = [f"- {line}" for line in evidence_lines] or ["- (none)"]
    return "\n".join(
        [
            f"Question: {ctx.request.question}",
            f"Outcome metric: {ctx.outcome_metric}",
            f"Degradation started at: {ctx.degradation_started_at}",
            "Anomalies:",
            *anomaly_block,
            "Evidence rows (values, % change, segments, timestamps):",
            *evidence_block,
            f"Existing hypotheses: {[h.statement for h in existing]}",
            f"Semantic neighbors (same entity cluster, not causes): {ctx.scratch.get('semantic_neighbors') or []}",
            f"Allowed catalogue metric ids: {ranked[:60]}",
            f"Max new hypotheses: {max(0, ctx.policies.budget('max_hypotheses') - len(existing))}",
        ]
    )
