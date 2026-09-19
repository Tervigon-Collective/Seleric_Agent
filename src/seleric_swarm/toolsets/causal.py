"""CausalToolset v0 — DoWhy estimate/refute over already-fetched evidence.

Frozen signatures: ``docs/refactor/CONTRACTS.md`` §4. Non-negotiable rules
4+5: calculate only; never fetch; never call another tool. ``search_breadth``
(A1.1) is the typed escalation ladder replacing hidden ``remediation_round``.
"""

from __future__ import annotations

from typing import Literal

from pydantic_ai import RunContext

from seleric_swarm.agent.artifacts import CausalArtifact, EvidenceArtifact
from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.causal.service import estimate_from_evidence
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.toolsets.policy_config import (
    DEFAULT_CAUSAL_ESTIMATOR,
    MIN_HISTORY_DAYS,
    MIN_OBSERVATION_ROWS,
    WARN_MISSING_CAUSAL_ARTIFACT,
    WARN_MISSING_TREATMENT_OUTCOME,
    WARN_NO_EVIDENCE,
    WARN_THIN_HISTORY,
    WARN_THIN_ROWS,
)

SearchBreadth = Literal[0, 1, 2]

_CALCULATION_VERSION = "causal.v0"


def _provenance(evidence_ids: list[str]) -> ArtifactProvenance:
    return ArtifactProvenance(
        evidence_ids=list(evidence_ids),
        calculation_version=_CALCULATION_VERSION,
    )


def _refuse(summary: str, *, error_code: str, warnings: list[str] | None = None) -> ToolResult:
    return ToolResult(
        success=False,
        summary=summary,
        error_code=error_code,
        retryable=False,
        warnings=list(warnings or []),
    )


def _load_evidence(
    ctx: RunContext[SelericDeps], evidence_ids: list[str]
) -> tuple[list[EvidenceArtifact], ToolResult | None]:
    if not evidence_ids:
        return [], _refuse(
            "no evidence_ids supplied",
            error_code="INSUFFICIENT_EVIDENCE",
            warnings=[WARN_NO_EVIDENCE],
        )

    artifacts = ctx.deps.artifact_store.get_many(list(evidence_ids))
    found = {a.id for a in artifacts}
    missing = [aid for aid in evidence_ids if aid not in found]
    if missing:
        return [], _refuse(
            f"evidence not found in store: {', '.join(missing)}",
            error_code="INSUFFICIENT_EVIDENCE",
            warnings=[WARN_NO_EVIDENCE],
        )

    evidence: list[EvidenceArtifact] = []
    for artifact in artifacts:
        if artifact.artifact_type != "evidence":
            return [], _refuse(
                f"artifact {artifact.id} is artifact_type={artifact.artifact_type!r}, not evidence",
                error_code="INSUFFICIENT_EVIDENCE",
                warnings=[WARN_NO_EVIDENCE],
            )
        try:
            evidence.append(EvidenceArtifact.model_validate(artifact.payload))
        except Exception as exc:
            return [], _refuse(
                f"artifact {artifact.id} payload is not a valid EvidenceArtifact: {exc}",
                error_code="INSUFFICIENT_EVIDENCE",
                warnings=[WARN_NO_EVIDENCE],
            )
    return evidence, None


def _precondition_history(
    evidence: list[EvidenceArtifact],
    *,
    treatment: str,
    outcome: str,
    search_breadth: SearchBreadth,
) -> ToolResult | None:
    """Base sufficiency gate — enough rows/span to estimate at all.

    ``search_breadth`` must *not* raise this floor (bug #6/#7): higher breadth
    widens the window/`candidate_cap` used inside ``estimate_from_evidence``,
    it does not refuse evidence that would have passed at breadth 0.
    """
    del search_breadth  # used only by estimate_from_evidence widening
    by_metric: dict[str, list[EvidenceArtifact]] = {}
    for e in evidence:
        by_metric.setdefault(e.metric_id, []).append(e)

    if treatment not in by_metric or outcome not in by_metric:
        return _refuse(
            f"evidence missing treatment={treatment!r} or outcome={outcome!r} series",
            error_code="INSUFFICIENT_EVIDENCE",
            warnings=[WARN_MISSING_TREATMENT_OUTCOME],
        )

    for metric_id in (treatment, outcome):
        series = by_metric[metric_id]
        if len(series) < MIN_OBSERVATION_ROWS:
            return _refuse(
                f"{metric_id} has {len(series)} points; need >={MIN_OBSERVATION_ROWS}",
                error_code="INSUFFICIENT_EVIDENCE",
                warnings=[WARN_THIN_ROWS],
            )
        start = min(e.period_start for e in series)
        end = max(e.period_end for e in series)
        span_days = max(0, (end - start).days) + 1
        if span_days < MIN_HISTORY_DAYS:
            return _refuse(
                f"{metric_id} spans {span_days}d; need >={MIN_HISTORY_DAYS}d",
                error_code="INSUFFICIENT_EVIDENCE",
                warnings=[WARN_THIN_HISTORY],
            )
    return None


def _write_causal(
    ctx: RunContext[SelericDeps],
    payload: CausalArtifact,
    *,
    evidence_ids: list[str],
) -> str:
    artifact = ctx.deps.artifact_store.put(
        Artifact(
            workspace_id=ctx.deps.principal.workspace_id,
            artifact_type="causal",
            payload=payload.model_dump(mode="json"),
            classification="derived",
            evidence_ids=list(evidence_ids),
            provenance=_provenance(evidence_ids),
            mission_id=ctx.deps.mission_id,
        )
    )
    return artifact.id


def estimate_effect(
    ctx: RunContext[SelericDeps],
    evidence_ids: list[str],
    treatment: str,
    outcome: str,
    method: str = DEFAULT_CAUSAL_ESTIMATOR,
    search_breadth: SearchBreadth = 0,
) -> ToolResult:
    """Estimate a causal effect from already-fetched evidence (frozen §4)."""
    evidence, refuse = _load_evidence(ctx, evidence_ids)
    if refuse is not None:
        return refuse

    gate = _precondition_history(
        evidence, treatment=treatment, outcome=outcome, search_breadth=search_breadth
    )
    if gate is not None:
        return gate

    outcome_est = estimate_from_evidence(
        evidence,
        treatment=treatment,
        outcome=outcome,
        method=method,
        search_breadth=search_breadth,
        as_of=ctx.deps.as_of,
    )

    causal = CausalArtifact(
        query=outcome_est.query,
        evidence_classification=outcome_est.evidence_classification,
        effect_estimate=outcome_est.effect_estimate,
        refutation_checks=list(outcome_est.refutation_checks),
        evidence_ids=list(evidence_ids),
        method=outcome_est.method,
    )
    aid = _write_causal(ctx, causal, evidence_ids=evidence_ids)
    effect_txt = (
        f"{outcome_est.effect_estimate:.4g}"
        if outcome_est.effect_estimate is not None
        else "n/a"
    )
    return ToolResult(
        success=True,
        artifact_ids=[aid],
        summary=(
            f"{treatment} → {outcome}: classification={outcome_est.evidence_classification}, "
            f"effect={effect_txt}, search_breadth={search_breadth}, "
            f"history_days={outcome_est.query.get('history_days')}, "
            f"candidate_cap={outcome_est.query.get('candidate_cap')}"
        ),
        provenance=_provenance(evidence_ids),
        warnings=list(outcome_est.warnings),
    )


def refute_estimate(ctx: RunContext[SelericDeps], causal_artifact_id: str) -> ToolResult:
    """Re-run refuters against a stored ``CausalArtifact``'s evidence (frozen §4)."""
    stored = ctx.deps.artifact_store.get(causal_artifact_id)
    if stored is None or stored.artifact_type != "causal":
        return _refuse(
            f"causal artifact not found: {causal_artifact_id}",
            error_code="INSUFFICIENT_EVIDENCE",
            warnings=[WARN_MISSING_CAUSAL_ARTIFACT],
        )
    try:
        prior = CausalArtifact.model_validate(stored.payload)
    except Exception as exc:
        return _refuse(
            f"invalid CausalArtifact payload: {exc}",
            error_code="INSUFFICIENT_EVIDENCE",
            warnings=[WARN_MISSING_CAUSAL_ARTIFACT],
        )

    evidence, refuse = _load_evidence(ctx, prior.evidence_ids)
    if refuse is not None:
        return refuse

    treatment = str(prior.query.get("treatment") or "")
    outcome = str(prior.query.get("outcome") or "")
    if not treatment or not outcome:
        return _refuse(
            "stored causal query missing treatment/outcome",
            error_code="INSUFFICIENT_EVIDENCE",
            warnings=[WARN_MISSING_TREATMENT_OUTCOME],
        )

    breadth_raw = prior.query.get("search_breadth", 0)
    try:
        search_breadth: SearchBreadth = int(breadth_raw)  # type: ignore[assignment]
        if search_breadth not in (0, 1, 2):
            search_breadth = 0
    except (TypeError, ValueError):
        search_breadth = 0

    method = prior.method or str(prior.query.get("method") or DEFAULT_CAUSAL_ESTIMATOR)
    outcome_est = estimate_from_evidence(
        evidence,
        treatment=treatment,
        outcome=outcome,
        method=method,
        search_breadth=search_breadth,
        as_of=ctx.deps.as_of,
    )
    causal = CausalArtifact(
        query={**prior.query, **outcome_est.query, "refute_of": causal_artifact_id},
        evidence_classification=outcome_est.evidence_classification,
        effect_estimate=outcome_est.effect_estimate,
        refutation_checks=list(outcome_est.refutation_checks),
        evidence_ids=list(prior.evidence_ids),
        method=outcome_est.method,
    )
    aid = _write_causal(ctx, causal, evidence_ids=prior.evidence_ids)
    return ToolResult(
        success=True,
        artifact_ids=[aid],
        summary=(
            f"refute {causal_artifact_id}: classification={outcome_est.evidence_classification}, "
            f"refutations={len(outcome_est.refutation_checks)}"
        ),
        provenance=_provenance(prior.evidence_ids),
        warnings=list(outcome_est.warnings),
    )
