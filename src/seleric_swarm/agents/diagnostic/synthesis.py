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
    DiagnosticContradiction,
    DiagnosticFinding,
    DiagnosticHypothesis,
    DiagnosticResult,
    FindingRole,
)
from seleric_swarm.agents.diagnostic.ontology import incident_type_for_treatment


def _contradictions_for(h: DiagnosticHypothesis) -> list[DiagnosticContradiction]:
    """Failed (non-skipped) tests ARE the contradiction evidence — this makes
    that reasoning inspectable instead of only affecting the posterior score.
    """
    out = []
    for r in h.test_results:
        if r.passed or r.detail.get("skipped"):
            continue
        out.append(DiagnosticContradiction.from_test_result(r, evidence_refs=list(h.supporting_evidence)))
    return out


def classify_hypothesis(ctx: DiagnosticContext, h: DiagnosticHypothesis) -> None:
    contradictions = _contradictions_for(h)
    if contradictions:
        ctx.scratch.setdefault("contradictions", []).extend(contradictions)
        h.contradictory_evidence = sorted({ref for c in contradictions for ref in c.evidence_refs})

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
    causal_results: list[tuple[DiagnosticHypothesis, str, CausalAnalysisArtifact]] | None = None,
) -> DiagnosticResult:
    causal_results = causal_results or []
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

    # Classify every causally-estimated candidate independently — a hypothesis
    # can be a real, reportable contributor without being THE explanation
    # (spec §54-55: primary/secondary/co-contributors, never forced to one).
    accepted: list[tuple[DiagnosticHypothesis, str, CausalAnalysisArtifact]] = []
    for h, confidence, artifact in causal_results:
        retained = ctx.policies.meets_retain(confidence) and confidence != "REJECTED"
        if retained:
            h.status = "retained"
            accepted.append((h, confidence, artifact))
        elif confidence == "REJECTED":
            h.status = "rejected"
            h.rejection_reason = "causal check rejected (e.g. impossible ordering)"
        else:
            h.status = "inconclusive"
            if ctx.policies.emit_inconclusive_finding():
                accepted.append((h, confidence, artifact))

    # Every other still-'testing' hypothesis (never causally estimated, or
    # estimated but not accepted above) is superseded by the accepted set.
    accepted_ids = {h.hypothesis_id for h, _, _ in accepted}
    for h in result.hypotheses:
        if h.hypothesis_id not in accepted_ids and h.status == "testing":
            h.status = "rejected"
            h.rejection_reason = h.rejection_reason or "not among the retained mechanisms; superseded"

    # Rank: retained beats inconclusive; within a tier, larger |estimated effect|
    # ranks first (a real-but-tiny contributor is a contributor, not the primary).
    def _effect_magnitude(artifact: CausalAnalysisArtifact | None) -> float:
        if artifact is None or artifact.estimated_effect is None:
            return 0.0
        return abs(artifact.estimated_effect)

    accepted.sort(key=lambda item: (0 if item[0].status == "retained" else 1, -_effect_magnitude(item[2])))

    ruled_out = [h.hypothesis_id for h in result.rejected()]
    findings: list[DiagnosticFinding] = []
    for idx, (h, confidence, artifact) in enumerate(accepted):
        role: FindingRole = "primary" if idx == 0 else ("secondary" if idx == 1 else "contributor")
        findings.append(
            DiagnosticFinding(
                statement=h.statement,
                mechanism=h.mechanism,
                causal_confidence=confidence,  # type: ignore[arg-type]
                causal_ref=artifact.causal_id if artifact else None,
                retained_hypothesis_id=h.hypothesis_id if h.status == "retained" else None,
                role=role,
                estimated_effect=artifact.estimated_effect if artifact else None,
                supporting_evidence=list(h.supporting_evidence),
                contradictory_evidence=list(h.contradictory_evidence),
                ruled_out=ruled_out,
                limitations=list(limitations),
            )
        )

    if not findings:
        if not result.hypotheses:
            limitations.append(
                "INSUFFICIENT_EVIDENCE: no candidate mechanism could be generated for "
                f"{ctx.outcome_metric or 'this metric'} from the available ontology/evidence."
            )
        else:
            limitations.append(
                "INSUFFICIENT_EVIDENCE: no hypothesis reached a causally-supported confidence tier."
            )

    # Residual uncertainty is real whenever the retained findings don't add up
    # to a single, dominant explanation: either several findings share credit,
    # or the sole finding never rose above metadata-only causal confirmation.
    result.residual_unexplained = len(findings) > 1 or (
        len(findings) == 1 and findings[0].retained_hypothesis_id is None
    )

    _apply_leadership_and_incident_type(ctx, result)

    result.finding = findings[0] if findings else None
    result.findings = findings
    result.causal_artifact = accepted[0][2] if accepted else None
    result.limitations = limitations
    result.contradictions = list(ctx.scratch.get("contradictions") or [])
    result.methodology = (
        "explicit hypotheses -> deterministic tests (evidence, temporal precedence, "
        "segment specificity, control divergence, dose-response) -> causal estimation + "
        "refutation on every surviving hypothesis -> retain/reject by confidence tier "
        "-> rank by effect magnitude into primary/secondary/contributor findings"
    )
    result.diagnostic_artifact = _to_diagnostic_artifact(ctx, result, result.causal_artifact)
    result.claims = _to_claims(ctx, result)
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


def _apply_leadership_and_incident_type(ctx: DiagnosticContext, result: DiagnosticResult) -> None:
    """Propose (never execute) a domain-leadership transfer, and attach a
    coarse incident_type label for downstream routing.

    Diagnostic only recommends; the Coordinator's Leadership Manager decides
    whether to honor it (spec §59, §128).
    """
    viable = [h for h in result.hypotheses if h.status != "rejected"]
    best = next(iter(result.retained()), None) or (viable[0] if viable else None)
    if best is None or not best.treatment_metric:
        return

    result.incident_type = incident_type_for_treatment(result.outcome_metric, best.treatment_metric)

    current_lead = (ctx.request.lead_domain or "").removesuffix("_agent") or None
    mechanism_domain = best.domains[0] if best.domains else None
    if mechanism_domain and current_lead and mechanism_domain != current_lead:
        result.recommended_domain_lead = f"{mechanism_domain}_agent"
        result.leadership_transfer_recommended = True
        result.leadership_transfer_reason = (
            f"Retained mechanism '{best.statement}' is owned by {mechanism_domain}, "
            f"not the current lead {current_lead}."
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


def _to_claims(ctx: DiagnosticContext, result: DiagnosticResult) -> list[Claim]:
    """One claim per *retained* finding — never for a merely inconclusive
    secondary/contributor (spec §67: Diagnostic proposes SUPPORTED claims only
    from validated mechanisms; Skeptic/Coordinator govern from there).
    """
    claims: list[Claim] = []
    domain = (ctx.request.lead_domain or "").removesuffix("_agent") or None
    for finding in result.findings:
        if finding.retained_hypothesis_id is None:
            continue
        causal_ref = finding.causal_ref
        causal_artifact = result.causal_artifact if causal_ref == (result.causal_artifact.causal_id if result.causal_artifact else None) else None
        claims.append(
            Claim(
                mission_id=ctx.request.mission_id,
                claim_type="causal",
                statement=finding.statement,
                origin_agent="diagnostic_agent",
                support_refs=list(finding.supporting_evidence),
                causal_refs=[causal_ref] if causal_ref else [],
                diagnostic_refs=[result.diagnostic_run_id],
                metadata={
                    "causal_confidence": finding.causal_confidence,
                    "role": finding.role,
                    "diagnosed_mechanism": f"{causal_artifact.treatment} -> {causal_artifact.outcome}"
                    if causal_artifact
                    else finding.mechanism,
                    "alternatives_ruled_out": bool(result.rejected()),
                    "domain": domain,
                },
            )
        )
    return claims
