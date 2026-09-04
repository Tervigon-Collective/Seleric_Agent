"""Evidence sufficiency validator (spec sec. 18).

A numeric/comparison/causal/forecast/recommendation/action claim that carries no
resolvable evidence fails here with a blocking challenge. Nothing downstream --
not the trust score, not the reasoning model -- can override that.
"""

from __future__ import annotations

from datetime import UTC, datetime

from seleric_swarm.agents.skeptic.context import SkepticContext, ValidatorOutcome
from seleric_swarm.agents.skeptic.validators.base import Validator, challenge, gap


class EvidenceValidator(Validator):
    name = "evidence"

    async def run(self, ctx: SkepticContext) -> ValidatorOutcome:
        out = self.outcome("OK")
        claim = ctx.claim
        evidence = ctx.evidence
        require = claim.claim_type in ctx.policies.require_evidence_for()

        resolved_ids = {e.evidence_id for e in evidence}
        dangling = [r for r in claim.support_refs if r not in resolved_ids and not _looks_artifact(r)]

        typed_support = (
            (claim.claim_type == "causal" and bool(ctx.causal))
            or (claim.claim_type == "forecast" and bool(ctx.forecasts))
            or (claim.claim_type in {"recommendation", "action"} and bool(ctx.strategies))
        )

        if require and not evidence and not typed_support:
            out.status = "REJECTED"
            out.challenges.append(
                challenge(
                    "evidence",
                    "blocking",
                    f"{claim.claim_type} claim has no resolvable supporting evidence.",
                    evidence_refs=claim.support_refs,
                    remediation_hint="Attach EvidenceArtifact refs from the Blackboard.",
                )
            )
            out.score_signals["evidence_quality"] = 0.0
            return out

        if dangling:
            out.status = "WEAK"
            out.challenges.append(
                challenge(
                    "evidence",
                    "warning",
                    f"{len(dangling)} support ref(s) could not be resolved: {dangling}",
                    evidence_refs=dangling,
                )
            )

        # freshness / sample-size / quality-flag checks per evidence row
        max_hours = ctx.policies.max_freshness_hours()
        min_sample = ctx.policies.min_sample_size()
        stale, small, flagged = [], [], []
        now = datetime.now(UTC)
        for ev in evidence:
            ts = _parse_ts(ev.freshness or ev.retrieved_at)
            if ts is not None and (now - ts).total_seconds() > max_hours * 3600:
                stale.append(ev.evidence_id)
            if ev.sample_size is not None and ev.sample_size < min_sample:
                small.append(ev.evidence_id)
            if any(f.upper() in {"INVALID", "PARTIAL", "SUSPECT", "LATE_DATA"} for f in ev.quality_flags):
                flagged.append(ev.evidence_id)

        if stale:
            out.status = _weaken(out.status)
            out.challenges.append(challenge("data_quality", "warning", f"Evidence older than {max_hours}h: {stale}", evidence_refs=stale))
        if small:
            out.status = _weaken(out.status)
            out.challenges.append(challenge("statistical", "warning", f"Evidence sample size below {min_sample}: {small}", evidence_refs=small))
        if flagged:
            out.status = _weaken(out.status)
            out.challenges.append(challenge("data_quality", "warning", f"Evidence carries quality flags: {flagged}", evidence_refs=flagged))

        # required typed artifacts present?
        if claim.claim_type == "causal" and not (ctx.causal or claim.causal_refs):
            out.status = _weaken(out.status)
            out.evidence_gaps.append(
                gap(
                    "No CausalAnalysisArtifact attached to a causal claim.",
                    "A causal claim cannot be validated without causal analysis (graph, estimator, refutations).",
                    capability_required="causal_diagnosis",
                    blocking=True,
                    priority=9,
                )
            )
        if claim.claim_type == "forecast" and not (ctx.forecasts or claim.forecast_refs):
            out.status = _weaken(out.status)
            out.evidence_gaps.append(
                gap(
                    "No ForecastArtifact attached to a forecast claim.",
                    "A forecast claim needs a registered model, interval and lineage.",
                    capability_required="forecasting",
                    blocking=True,
                    priority=9,
                )
            )

        got = len(evidence)
        out.score_signals["evidence_quality"] = max(0.0, min(1.0, 0.35 + 0.2 * got - 0.15 * len(stale + small + flagged)))
        out.detail = {"evidence_count": got, "stale": stale, "small_sample": small, "flagged": flagged, "dangling": dangling}
        return out


def _looks_artifact(ref: str) -> bool:
    return any(ref.startswith(p) for p in ("CAUS", "PRED", "STRAT", "AN-", "HYP", "SKEP"))


def _weaken(status: str) -> str:
    return "WEAK" if status == "OK" else status


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(normalized)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None
