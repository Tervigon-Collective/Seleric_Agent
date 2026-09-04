"""Synthesis: retain/reject hypotheses, pick the root cause, build outputs.

Retain/reject is deterministic:
  * any failed HARD-GATE test  -> rejected
  * else score = fraction of non-skipped tests passed
  * a hypothesis is *eligible* for the causal step if score >= 0.5 and it has a
    treatment metric
  * the causal-estimated hypothesis is RETAINED iff its confidence tier meets
    ``policies.retain_threshold()``; otherwise it is 'inconclusive'
  * every other eligible hypothesis whose causal frontier is not tested is left
    'testing'; hypotheses with score < 0.5 become 'rejected'
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.agents.diagnostic.context import DiagnosticContext
from seleric_swarm.agents.diagnostic.contracts import (
    CausalAnalysisArtifact,
    Claim,
    DiagnosticArtifact,
    DiagnosticFinding,
    DiagnosticHypothesis,
    DiagnosticResult,
)


def classify_hypothesis(ctx: DiagnosticContext, h: DiagnosticHypothesis) -> None:
    hard_fail = next((r for r in h.test_results if r.hard_gate and not r.passed), None)
    if hard_fail:
        h.status = "rejected"
        h.rejection_reason = f"hard gate failed: {hard_fail.kind} - {hard_fail.note}"
        h.posterior_score = 0.0
        return
    scored = [r for r in h.test_results if not r.detail.get("skipped")]
    if not scored:
        h.posterior_score = round(0.4 + 0.3 * min(1.0, len(h.supporting_evidence) / 2), 4)
    else:
        h.posterior_score = round(sum(1 for r in scored if r.passed) / len(scored), 4)
    if h.posterior_score < 0.5 and not h.is_primary:
        h.status = "rejected"
        h.rejection_reason = f"only {h.posterior_score:.0%} of tests passed"
    else:
        h.status = "testing"


def finalize(
    ctx: DiagnosticContext,
    result: DiagnosticResult,
    *,
    causal_hypothesis: DiagnosticHypothesis | None,
    causal_artifact: CausalAnalysisArtifact | None,
    causal_confidence: str | None,
) -> DiagnosticResult:
    limitations: list[str] = []
    if ctx.policies.always_note_confounding():
        limitations.append("Unmeasured confounding cannot be completely excluded.")
    if ctx.synthetic_inputs():
        limitations.append(
            "Inputs are SYNTHETIC (fixture/template). Treat the diagnosis as a methodology "
            "demonstration, not a business conclusion."
        )
    if ctx.request.observations is None:
        limitations.append("Causal estimate is metadata-only (no observation frame was fitted).")

    finding: DiagnosticFinding | None = None
    if causal_hypothesis is not None and causal_confidence is not None:
        retained = ctx.policies.meets_retain(causal_confidence) and causal_confidence != "REJECTED"
        if retained:
            causal_hypothesis.status = "retained"
        elif causal_confidence == "REJECTED":
            causal_hypothesis.status = "rejected"
            causal_hypothesis.rejection_reason = "causal check rejected (e.g. impossible ordering)"
        else:
            causal_hypothesis.status = "inconclusive"

        # every other still-'testing' hypothesis is now rejected as an alternative
        for h in result.hypotheses:
            if h.hypothesis_id != causal_hypothesis.hypothesis_id and h.status == "testing":
                h.status = "rejected"
                h.rejection_reason = h.rejection_reason or "not the retained mechanism; superseded"

        if retained or (ctx.policies.emit_inconclusive_finding() and causal_confidence != "REJECTED"):
            finding = DiagnosticFinding(
                statement=causal_hypothesis.statement,
                mechanism=causal_hypothesis.mechanism,
                causal_confidence=causal_confidence,  # type: ignore[arg-type]
                causal_ref=causal_artifact.causal_id if causal_artifact else None,
                retained_hypothesis_id=causal_hypothesis.hypothesis_id if retained else None,
                supporting_evidence=list(causal_hypothesis.supporting_evidence),
                ruled_out=[h.hypothesis_id for h in result.rejected()],
                limitations=list(limitations),
            )

    result.finding = finding
    result.causal_artifact = causal_artifact
    result.limitations = limitations
    result.methodology = (
        "explicit hypotheses -> deterministic tests (evidence, temporal precedence, "
        "segment specificity, control divergence, dose-response) -> causal estimation + "
        "refutation on the top surviving hypothesis -> retain/reject by confidence tier"
    )
    result.diagnostic_artifact = _to_diagnostic_artifact(ctx, result, causal_artifact)
    result.claims = _to_claims(ctx, result, finding, causal_artifact)
    result.synthetic = ctx.synthetic_inputs()
    return result


def _to_diagnostic_artifact(
    ctx: DiagnosticContext, result: DiagnosticResult, causal_artifact: CausalAnalysisArtifact | None
) -> DiagnosticArtifact:
    return DiagnosticArtifact(
        diagnostic_id=result.diagnostic_run_id,
        mission_id=ctx.request.mission_id,
        hypotheses=[_hypo_row(h) for h in result.hypotheses],
        retained_hypotheses=[h.hypothesis_id for h in result.retained()],
        rejected_hypotheses=[h.hypothesis_id for h in result.rejected()],
        supporting_evidence=sorted({e for h in result.retained() for e in h.supporting_evidence}),
        contradictory_evidence=sorted({e for h in result.hypotheses for e in h.contradictory_evidence}),
        methodology=result.methodology,
        limitations=result.limitations,
        causal_ref=causal_artifact.causal_id if causal_artifact else None,
        synthetic=result.synthetic,
    )


def _hypo_row(h: DiagnosticHypothesis) -> dict[str, Any]:
    return {
        "hypothesis_id": h.hypothesis_id,
        "statement": h.statement,
        "mechanism": h.mechanism,
        "treatment_metric": h.treatment_metric,
        "status": h.status,
        "prior_score": h.prior_score,
        "posterior_score": h.posterior_score,
        "is_primary": h.is_primary,
        "llm_generated": h.llm_generated,
        "rejection_reason": h.rejection_reason,
        "tests": [
            {"kind": r.kind, "passed": r.passed, "hard_gate": r.hard_gate, "note": r.note}
            for r in h.test_results
        ],
    }


def _to_claims(
    ctx: DiagnosticContext,
    result: DiagnosticResult,
    finding: DiagnosticFinding | None,
    causal_artifact: CausalAnalysisArtifact | None,
) -> list[Claim]:
    if finding is None or finding.retained_hypothesis_id is None:
        return []
    return [
        Claim(
            mission_id=ctx.request.mission_id,
            claim_type="causal",
            statement=finding.statement,
            origin_agent="diagnostic_agent",
            support_refs=list(finding.supporting_evidence),
            causal_refs=[causal_artifact.causal_id] if causal_artifact else [],
            diagnostic_refs=[result.diagnostic_run_id],
            metadata={
                "causal_confidence": finding.causal_confidence,
                "diagnosed_mechanism": f"{causal_artifact.treatment} -> {causal_artifact.outcome}"
                if causal_artifact
                else finding.mechanism,
                "alternatives_ruled_out": bool(result.rejected()),
                "domain": (ctx.request.lead_domain or "").removesuffix("_agent") or None,
            },
        )
    ]
