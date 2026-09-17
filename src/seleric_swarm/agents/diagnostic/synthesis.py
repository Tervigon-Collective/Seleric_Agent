"""Synthesis: retain/reject causal candidates, build outputs.

Retain/reject is deterministic and driven entirely by the DoWhy confidence
tier (no LLM/test-battery gate in between):

  * a candidate is RETAINED iff its causal confidence tier meets
    ``policies.retain_threshold()``
  * a candidate whose causal check is temporally/structurally impossible is
    REJECTED (``_confidence`` in ``causal/estimator.py`` already enforces this)
  * everything else surviving is 'inconclusive' and still reported (never
    silently dropped) when ``emit_inconclusive_finding`` is set
  * every candidate the causal graph proposed but that never got a causal
    estimate (e.g. no observation column) is rejected, superseded by the
    accepted set

``narratives`` (one per accepted candidate, from ``scenarios.py``'s LLM step)
become the finding's statement/mechanism -- the LLM's only contribution here
is narration of an already-decided result, never the decision itself.
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
    FindingRole,
)
from seleric_swarm.services.metrics import MetricRegistry

_METRICS: MetricRegistry | None = None


def _metrics() -> MetricRegistry:
    global _METRICS
    if _METRICS is None:
        _METRICS = MetricRegistry("config/metric_registry.yaml")
    return _METRICS


_UP_WORDS = ("increase", "increas", "rise", "risen", "rising", "grew", "growing", "growth", "higher", "up", "spike", "surge")
_DOWN_WORDS = ("decrease", "decreas", "drop", "dropped", "fell", "fallen", "falling", "declin", "lower", "down", "dip", "plunge")


def _question_implies_direction(question: str) -> str | None:
    q = question.lower()
    up = any(w in q for w in _UP_WORDS)
    down = any(w in q for w in _DOWN_WORDS)
    if up and not down:
        return "up"
    if down and not up:
        return "down"
    return None


def _direction_mismatch_note(ctx: DiagnosticContext) -> str | None:
    """The question can assert a direction ("why has X increased") that the
    data doesn't actually show. Silently diagnosing against that premise
    without saying so produces a confusing, self-contradicting answer (a
    narrative about a "rise" next to an anomaly reporting a drop) -- flag the
    mismatch instead of assuming the question's premise is correct.
    """
    implied = _question_implies_direction(ctx.request.question)
    if implied is None:
        return None
    actual = next((a.direction for a in ctx.anomalies if a.metric_id == ctx.outcome_metric), None)
    if actual not in {"up", "down"} or actual == implied:
        return None
    return (
        f"The question assumes {ctx.outcome_metric} went {implied}, but the observed data for this "
        f"window shows it actually went {actual}. Treat the diagnosis below as explaining the "
        f"observed ({actual}) movement, not the premise in the question."
    )


def finalize(
    ctx: DiagnosticContext,
    result: DiagnosticResult,
    *,
    causal_results: list[tuple[DiagnosticHypothesis, str, CausalAnalysisArtifact]] | None = None,
    narratives: dict[str, str] | None = None,
) -> DiagnosticResult:
    causal_results = causal_results or []
    narratives = narratives or {}
    limitations: list[str] = []
    mismatch = _direction_mismatch_note(ctx)
    if mismatch:
        limitations.append(mismatch)
    if ctx.policies.always_note_confounding():
        limitations.append("Unmeasured confounding cannot be completely excluded.")
    if ctx.synthetic_inputs():
        limitations.append(
            "Inputs are SYNTHETIC (fixture/template). Treat the diagnosis as a methodology "
            "demonstration, not a business conclusion."
        )
    if ctx.request.observations is None:
        limitations.append("Causal estimate is metadata-only (no observation frame was fitted).")

    # Classify every causally-estimated candidate independently -- a node can
    # be a real, reportable contributor without being THE explanation
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

    # Every candidate not accepted above -- rejected by the causal check, or
    # never causally estimated at all (e.g. no observation column) -- is
    # superseded by the accepted set.
    accepted_ids = {h.hypothesis_id for h, _, _ in accepted}
    estimated_ids = {h.hypothesis_id for h, _, _ in causal_results}
    for h in result.hypotheses:
        if h.hypothesis_id in accepted_ids:
            continue
        if h.hypothesis_id in estimated_ids:
            continue  # already marked rejected/inconclusive above
        h.status = "rejected"
        h.rejection_reason = h.rejection_reason or "no causal estimate produced (missing observation data)"

    # Rank: retained beats inconclusive; within a tier, larger |estimated effect|
    # ranks first (a real-but-tiny contributor is a contributor, not the primary).
    def _effect_magnitude(artifact: CausalAnalysisArtifact | None) -> float:
        if artifact is None or artifact.estimated_effect is None:
            return 0.0
        return abs(artifact.estimated_effect)

    accepted.sort(key=lambda item: (0 if item[0].status == "retained" else 1, -_effect_magnitude(item[2])))

    ruled_out = [h.hypothesis_id for h in result.hypotheses if h.status == "rejected"]
    findings: list[DiagnosticFinding] = []
    for idx, (h, confidence, artifact) in enumerate(accepted):
        role: FindingRole = "primary" if idx == 0 else ("secondary" if idx == 1 else "contributor")
        narrative = narratives.get(h.hypothesis_id) or h.statement
        # The Blackboard Hypothesis artifact (posted from result.hypotheses,
        # not from DiagnosticFinding) is what the Coordinator's claim-gate and
        # the mission's "leading hypothesis" display text read directly — if
        # the narrative isn't written back onto the hypothesis itself, every
        # caller outside this module keeps seeing the bland discovery-time
        # template ("X is upstream on the causal graph of Y") instead of the
        # scenario narrative, even though a real one was generated.
        h.statement = narrative
        findings.append(
            DiagnosticFinding(
                statement=narrative,
                mechanism=narrative,
                causal_confidence=confidence,  # type: ignore[arg-type]
                causal_ref=artifact.causal_id if artifact else None,
                retained_hypothesis_id=h.hypothesis_id if h.status == "retained" else None,
                role=role,
                estimated_effect=artifact.estimated_effect if artifact else None,
                supporting_evidence=list(h.supporting_evidence),
                ruled_out=ruled_out,
                limitations=list(limitations),
            )
        )

    if not findings:
        if not result.hypotheses:
            limitations.append(
                "INSUFFICIENT_EVIDENCE: no candidate node was found on the causal graph for "
                f"{ctx.outcome_metric or 'this metric'}."
            )
        else:
            limitations.append(
                "INSUFFICIENT_EVIDENCE: no candidate reached a causally-supported confidence tier."
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
    result.causal_artifacts = [art for _, _, art in accepted if art is not None]
    result.causal_artifact = result.causal_artifacts[0] if result.causal_artifacts else None
    result.limitations = limitations
    result.contradictions = []
    result.methodology = (
        "causal-graph ancestor discovery -> DoWhy estimation + refutation on every candidate node "
        "-> retain/reject by confidence tier -> rank by effect magnitude into "
        "primary/secondary/contributor findings -> LLM scenario narration of confirmed nodes only"
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
        contradictory_evidence=[],
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
    best = next(iter(result.retained()), None)
    if best is None or not best.treatment_metric:
        return

    result.incident_type = best.domains[0] if best.domains else None

    current_lead = (ctx.request.lead_domain or "").removesuffix("_agent") or None
    mechanism_domain = best.domains[0] if best.domains else None
    if not (mechanism_domain and current_lead and mechanism_domain != current_lead):
        return

    # Stay on the domain that owns the user's asked metric. A funnel co-mover
    # driving ROAS is a finding, not a reason to abandon the performance question.
    asked = (ctx.request.primary_metric or ctx.request.outcome_metric or result.outcome_metric or "").strip()
    asked_owner = (_metrics().owner_agent_for(asked) or "").removesuffix("_agent") if asked else ""
    if asked_owner and asked_owner == current_lead:
        return

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
        "is_primary": h.is_primary,
        "rejection_reason": h.rejection_reason,
    }


def _to_claims(ctx: DiagnosticContext, result: DiagnosticResult) -> list[Claim]:
    """One claim per *retained* finding -- never for a merely inconclusive
    secondary/contributor (spec §67: Diagnostic proposes SUPPORTED claims only
    from validated mechanisms; Skeptic/Coordinator govern from there).
    """
    claims: list[Claim] = []
    domain = (ctx.request.lead_domain or "").removesuffix("_agent") or None
    by_id = {a.causal_id: a for a in result.causal_artifacts}
    for finding in result.findings:
        if finding.retained_hypothesis_id is None:
            continue
        causal_ref = finding.causal_ref
        causal_artifact = by_id.get(causal_ref) if causal_ref else None
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
