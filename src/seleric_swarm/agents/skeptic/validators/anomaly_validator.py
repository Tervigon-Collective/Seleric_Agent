"""Anomaly validator (spec sec. 25)."""

from __future__ import annotations

from seleric_swarm.agents.skeptic.context import SkepticContext, ValidatorOutcome
from seleric_swarm.agents.skeptic.validators.base import Validator, challenge, gap

_MIN_HISTORY_DAYS = 28
_MIN_SAMPLE = 100


class AnomalyValidator(Validator):
    name = "anomaly"

    async def run(self, ctx: SkepticContext) -> ValidatorOutcome:
        out = self.outcome("OK")
        if not ctx.anomalies:
            out.status = "NOT_APPLICABLE"
            out.evidence_gaps.append(
                gap(
                    "Anomaly claim without an AnomalyArtifact.",
                    "Cannot audit a claimed anomaly with no detector output.",
                    capability_required="anomaly_analysis",
                    blocking=ctx.claim.claim_type == "anomaly",
                    priority=8,
                )
            )
            return out

        verdicts: list[str] = []
        for an in ctx.anomalies:
            issues: list[str] = []
            if not an.detector_id:
                issues.append("no detector id")
            if an.detector_id and not an.detector_version:
                issues.append("no detector version")
            if an.observed is None or (an.expected is None and not an.expected_range):
                issues.append("missing observed/expected baseline")

            history = an.history_days if an.history_days is not None else ctx.claim.metadata.get("history_days")
            sample = an.sample_size if an.sample_size is not None else ctx.claim.metadata.get("sample_size")
            not_enough = (history is not None and history < _MIN_HISTORY_DAYS) or (
                sample is not None and sample < _MIN_SAMPLE
            )
            weak_magnitude = an.deviation_pct is not None and abs(an.deviation_pct) < 5.0
            seasonality_gap = an.seasonality_handled is False

            if issues and "missing observed/expected baseline" in issues:
                verdict = "INVALID_DETECTOR_FOR_CONTEXT"
            elif not_enough:
                verdict = "NOT_ENOUGH_DATA"
            elif weak_magnitude:
                verdict = "WEAK_ANOMALY"
            else:
                verdict = "SUPPORTED_ANOMALY"
            verdicts.append(verdict)

            if verdict == "NOT_ENOUGH_DATA":
                out.status = "WEAK"
                out.evidence_gaps.append(
                    gap(
                        f"Anomaly on {an.metric_id} rests on insufficient history/sample "
                        f"(history={history}, sample={sample}).",
                        "Detectors need enough baseline to separate signal from noise/seasonality.",
                        capability_required="metric_observation",
                        blocking=False,
                        priority=7,
                    )
                )
                out.challenges.append(challenge("anomaly", "warning", f"{an.metric_id}: NOT_ENOUGH_DATA", evidence_refs=an.evidence_refs))
            elif verdict == "WEAK_ANOMALY":
                out.status = "WEAK"
                out.challenges.append(challenge("anomaly", "warning", f"{an.metric_id}: deviation {an.deviation_pct}% is within normal variation.", evidence_refs=an.evidence_refs))
            elif verdict == "INVALID_DETECTOR_FOR_CONTEXT":
                out.status = "REJECTED"
                out.challenges.append(challenge("anomaly", "blocking", f"{an.metric_id}: {issues}", evidence_refs=an.evidence_refs))
            if seasonality_gap:
                out.methodological_issues.append(f"Anomaly on {an.metric_id} did not account for seasonality.")

        supported = sum(1 for v in verdicts if v == "SUPPORTED_ANOMALY")
        out.score_signals["anomaly_support"] = supported / max(1, len(verdicts))
        out.detail = {"verdicts": verdicts}
        return out
