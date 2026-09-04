"""Resolve a :class:`SkepticValidationRequest` into a fully-loaded
:class:`SkepticContext`.

Pulls evidence + artifacts from the injected repositories, adapts the lean
Blackboard shape to the Skeptic's contracts, and gathers related evidence for
contradiction search. Missing refs are recorded (not fatal) so the evidence
validator can raise the appropriate gap.
"""

from __future__ import annotations

from seleric_swarm.agents.skeptic.context import SkepticContext, SkepticDeps
from seleric_swarm.agents.skeptic.contracts import (
    AnomalyArtifact,
    CausalAnalysisArtifact,
    Claim,
    DiagnosticArtifact,
    EvidenceArtifact,
    ForecastArtifact,
    SkepticValidationRequest,
    StrategyArtifact,
)
from seleric_swarm.agents.skeptic.policies import SkepticPolicies
from seleric_swarm.agents.skeptic.registries import ArtifactRepository, EvidenceRepository

_ARTIFACT_KEYS = {
    "anomaly": ("anomaly", "anomaly_id", "AN"),
    "causal": ("causal", "causal_id", "CAUS"),
    "prediction": ("prediction", "forecast_id", "PRED"),
    "strategy": ("strategy", "strategy_id", "STRAT"),
    "diagnostic": ("diagnostic", "diagnostic_id", "DIAG"),
}


async def resolve_context(
    request: SkepticValidationRequest,
    *,
    evidence_repo: EvidenceRepository,
    artifact_repo: ArtifactRepository,
    deps: SkepticDeps,
    policies: SkepticPolicies,
) -> SkepticContext:
    claim: Claim = request.claim
    mission_id = request.mission_id or claim.mission_id

    # -- evidence ------------------------------------------------------
    ev_ids: list[str] = []
    for ref in [*claim.support_refs, *request.evidence_refs]:
        if ref and ref not in ev_ids:
            ev_ids.append(ref)
    ev_rows = await evidence_repo.get_many(ev_ids)
    evidence = [EvidenceArtifact.from_blackboard(r) for r in ev_rows]

    related_rows = await evidence_repo.search_related(mission_id)
    known = {e.evidence_id for e in evidence}
    related = [
        EvidenceArtifact.from_blackboard(r)
        for r in related_rows
        if (r.get("evidence_id") or r.get("artifact_id")) not in known
    ]

    # -- typed artifacts by explicit ref -----------------------------
    anomalies: list[AnomalyArtifact] = []
    causal: list[CausalAnalysisArtifact] = []
    forecasts: list[ForecastArtifact] = []
    strategies: list[StrategyArtifact] = []
    diagnostics: list[DiagnosticArtifact] = []
    missing: list[str] = []

    async def _load(ref: str):
        row = await artifact_repo.get(ref)
        if row is None:
            missing.append(ref)
        return row

    for ref in claim.anomaly_refs:
        row = await _load(ref)
        if row:
            anomalies.append(AnomalyArtifact.from_blackboard(row))
    for ref in claim.causal_refs:
        row = await _load(ref)
        if row:
            causal.append(CausalAnalysisArtifact.from_blackboard(row))
    for ref in [*claim.forecast_refs, *claim.model_refs]:
        row = await _load(ref)
        if row:
            forecasts.append(ForecastArtifact.from_blackboard(row))
    for ref in claim.strategy_refs:
        row = await _load(ref)
        if row:
            strategies.append(StrategyArtifact.from_blackboard(row))
    for ref in claim.diagnostic_refs:
        row = await _load(ref)
        if row:
            diagnostics.append(DiagnosticArtifact.model_validate(row))

    # -- fall back to mission-wide artifacts when the claim under-specifies ---
    if not causal and claim.claim_type in {"causal", "correlation"}:
        causal = [CausalAnalysisArtifact.from_blackboard(r) for r in await artifact_repo.by_type(mission_id, "causal")]
    if not forecasts and claim.claim_type == "forecast":
        forecasts = [ForecastArtifact.from_blackboard(r) for r in await artifact_repo.by_type(mission_id, "prediction")]
    if not strategies and claim.claim_type in {"recommendation", "action"}:
        strategies = [StrategyArtifact.from_blackboard(r) for r in await artifact_repo.by_type(mission_id, "strategy")]
    if not anomalies:
        anomalies = [AnomalyArtifact.from_blackboard(r) for r in await artifact_repo.by_type(mission_id, "anomaly")]

    ctx = SkepticContext(
        claim=claim,
        policies=policies,
        deps=deps,
        evidence=evidence,
        related_evidence=related,
        anomalies=anomalies,
        causal=causal,
        diagnostics=diagnostics,
        forecasts=forecasts,
        strategies=strategies,
        risk_context=dict(request.risk_context),
    )
    ctx.alternative_context = {"missing_artifact_refs": missing}
    return ctx
