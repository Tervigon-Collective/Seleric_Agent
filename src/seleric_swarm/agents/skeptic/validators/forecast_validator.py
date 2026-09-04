"""Forecast validator (spec sec. 30).

Complements the model validator: audits the forecast *statement* itself --
target, horizon, interval, regime applicability, scenario assumptions -- and
enforces the no-LLM-numeric-forecast fallback policy.
"""

from __future__ import annotations

from seleric_swarm.agents.skeptic.context import SkepticContext, ValidatorOutcome
from seleric_swarm.agents.skeptic.validators.base import Validator, challenge, followup, gap


class ForecastValidator(Validator):
    name = "forecast"

    async def run(self, ctx: SkepticContext) -> ValidatorOutcome:
        out = self.outcome("OK")
        if not ctx.forecasts:
            out.status = "INSUFFICIENT"
            out.evidence_gaps.append(
                gap(
                    "Forecast claim without a ForecastArtifact.",
                    "A numeric forecast needs a registered model or approved statistical baseline.",
                    capability_required="forecasting",
                    blocking=True,
                    priority=9,
                )
            )
            out.followups.append(
                followup(
                    "forecasting",
                    "Produce a forecast from a registered model or approved baseline.",
                    f"Forecast the target for: {ctx.claim.statement} - include interval, horizon and model lineage.",
                    priority=8,
                    blocking=True,
                )
            )
            out.score_signals["forecast_quality"] = 0.1
            return out

        fc = ctx.forecasts[0]
        issues: list[str] = []

        if fc.llm_generated and not ctx.policies.forecast_flag("allow_llm_numeric_fallback"):
            out.status = "REJECTED"
            out.challenges.append(
                challenge(
                    "forecast",
                    "blocking",
                    "Numeric forecast was produced by an LLM; policy forbids LLM numeric fallback.",
                    evidence_refs=[fc.forecast_id],
                    remediation_hint="Route to a registered production model or an approved statistical baseline.",
                )
            )

        if not fc.interval or len(fc.interval) != 2:
            out.status = _weaken(out.status)
            out.challenges.append(challenge("forecast", "warning", "Forecast has no prediction interval.", evidence_refs=[fc.forecast_id]))
            issues.append("no interval")

        if not fc.horizon:
            out.status = _weaken(out.status)
            issues.append("no horizon")

        if fc.applicability_status and fc.applicability_status.lower() in {"out_of_domain", "regime_shift", "not_applicable"}:
            out.status = "REJECTED"
            out.challenges.append(
                challenge("forecast", "blocking", f"Forecast applicability: {fc.applicability_status}.", evidence_refs=[fc.forecast_id])
            )

        if not fc.backtest_metrics:
            out.status = _weaken(out.status)
            issues.append("no backtest metrics")

        interval_quality = 1.0
        if fc.interval and len(fc.interval) == 2 and fc.prediction is not None:
            try:
                width = abs(float(fc.interval[1]) - float(fc.interval[0]))
                mid = abs(float(fc.prediction)) or 1.0
                interval_quality = max(0.1, 1.0 - min(1.0, width / (2 * mid)))
            except (TypeError, ValueError):
                interval_quality = 0.5

        out.score_signals["forecast_quality"] = 0.2 if out.status == "REJECTED" else (0.85 if not issues else 0.55)
        out.score_signals["interval_quality"] = interval_quality
        out.score_signals["backtest_quality"] = 0.8 if fc.backtest_metrics else 0.3
        out.score_signals["drift_status"] = 0.1 if (fc.drift_status or "").lower() in ctx.policies.drift_reject_statuses() else 0.85
        out.detail = {"issues": issues, "llm_generated": fc.llm_generated}
        return out


def _weaken(status: str) -> str:
    if status == "REJECTED":
        return status
    return "WEAK" if status == "OK" else status
