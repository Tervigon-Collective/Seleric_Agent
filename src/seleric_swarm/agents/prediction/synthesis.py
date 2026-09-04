"""Synthesis: turn a ``ForecastRun`` + applicability into a ``ForecastArtifact``,
a confidence tier, scenarios, limitations and forecast ``Claim[]``.
"""

from __future__ import annotations

from seleric_swarm.agents.prediction.context import PredictionContext
from seleric_swarm.agents.prediction.contracts import (
    ApplicabilityStatus,
    Claim,
    ForecastArtifact,
    ForecastRun,
    PredictionResult,
    PredictiveConfidence,
)
from seleric_swarm.agents.prediction.forecasting import build_scenarios


def build_insufficient(ctx: PredictionContext, reasons: list[str]) -> PredictionResult:
    return PredictionResult(
        mission_id=ctx.request.mission_id,
        target_metric=ctx.target_metric,
        horizon=ctx.horizon,
        source="insufficient",
        applicability="unknown",
        confidence="INSUFFICIENT_PREDICTIVE_EVIDENCE",
        methodology="fallback ladder exhausted: no registered production model and no approved baseline could serve this target",
        limitations=[
            "INSUFFICIENT_PREDICTIVE_EVIDENCE - no forecast was produced.",
            *reasons,
        ],
        audit={"fallback_reasons": reasons},
        synthetic=ctx.synthetic_inputs(),
    )


def finalize(
    ctx: PredictionContext,
    run: ForecastRun,
    applicability: ApplicabilityStatus,
    applic_notes: list[str],
    fallback_reasons: list[str],
) -> PredictionResult:
    limitations: list[str] = list(run.notes)
    if ctx.synthetic_inputs() or run.synthetic:
        limitations.append(
            "Inputs are SYNTHETIC (fixture/template). Treat the forecast as a methodology "
            "demonstration, not a business projection."
        )
    if fallback_reasons and run.source == "statistical_baseline":
        limitations.append("Used the approved statistical baseline: " + "; ".join(fallback_reasons))
    limitations.extend(applic_notes)

    interval_ok = len(run.interval) == 2
    if ctx.policies.interval_required() and not interval_ok:
        limitations.append("Forecast has no prediction interval.")

    rel_width = _relative_width(run)
    drift = (run.drift_status or "").lower()

    confidence: PredictiveConfidence = _confidence(ctx, run, applicability, rel_width, drift, interval_ok)

    artifact = ForecastArtifact(
        forecast_id=f"PRED-{abs(hash((ctx.mission_key(), run.method, run.target_metric))) % 10**10}",
        mission_id=ctx.request.mission_id,
        target_metric=run.target_metric,
        prediction=run.prediction,
        interval=[float(x) for x in run.interval] if interval_ok else [],
        horizon=run.horizon,
        model_id=run.model_id,
        model_version=run.model_version,
        feature_set_id=run.feature_set_id,
        feature_set_version=run.feature_set_version,
        training_window=run.training_window,
        backtest_metrics=run.backtest_metrics,
        drift_status=run.drift_status,
        applicability_status=applicability,
        generated_at=None,
        evidence_refs=list(ctx.request.evidence_refs),
        limitations=limitations,
        llm_generated=False,          # numbers never come from an LLM
        synthetic=bool(run.synthetic or ctx.synthetic_inputs()),
    )

    scenarios = build_scenarios(ctx, run)

    result = PredictionResult(
        mission_id=ctx.request.mission_id,
        target_metric=run.target_metric,
        horizon=run.horizon,
        source=run.source,
        applicability=applicability,
        confidence=confidence,
        forecast_artifact=artifact,
        scenarios=scenarios,
        methodology=(
            f"forecast orchestration: fallback ladder -> {run.method}; "
            f"applicability={applicability}; scenarios from the model interval + cause persistence"
        ),
        limitations=limitations,
        synthetic=artifact.synthetic,
        audit={
            "method": run.method,
            "fallback_reasons": fallback_reasons,
            "relative_interval_width": rel_width,
            "drift_status": run.drift_status,
            "cause_persistence": ctx.cause_persistence,
            "causal_supported": ctx.causal_supported,
        },
    )
    result.claims = _claims(ctx, result, artifact, confidence)
    return result


def _confidence(
    ctx: PredictionContext,
    run: ForecastRun,
    applicability: str,
    rel_width: float | None,
    drift: str,
    interval_ok: bool,
) -> PredictiveConfidence:
    if applicability in ctx.policies.applic_reject_statuses():
        return "INSUFFICIENT_PREDICTIVE_EVIDENCE"
    if drift in ctx.policies.drift_reject_statuses():
        return "INSUFFICIENT_PREDICTIVE_EVIDENCE"
    if not interval_ok and ctx.policies.interval_required():
        return "WEAK"

    mape = _mape(run)
    tight = rel_width is not None and rel_width <= ctx.policies.interval_max_relative_width()

    if run.source == "registered_model" and tight and mape is not None and mape < ctx.policies.strong_backtest_mape_below():
        return "STRONG"
    if run.source == "registered_model" and tight:
        return "MODERATE"
    if run.source == "statistical_baseline" and tight:
        return "MODERATE"
    return "WEAK"


def _claims(
    ctx: PredictionContext,
    result: PredictionResult,
    artifact: ForecastArtifact,
    confidence: str,
) -> list[Claim]:
    if confidence == "INSUFFICIENT_PREDICTIVE_EVIDENCE" or artifact.prediction is None:
        return []
    interval = artifact.interval
    tail = f" (interval {interval})" if len(interval) == 2 else ""
    return [
        Claim(
            mission_id=ctx.request.mission_id,
            claim_type="forecast",
            statement=(
                f"{artifact.target_metric} is projected at {artifact.prediction} over {artifact.horizon}{tail}"
            ),
            origin_agent="prediction_agent",
            support_refs=list(ctx.request.evidence_refs),
            forecast_refs=[artifact.forecast_id],
            model_refs=[artifact.model_id] if artifact.model_id else [],
            metadata={
                "predictive_confidence": confidence,
                "source": result.source,
                "applicability": result.applicability,
                "available_history_days": len(ctx.history) or None,
                "domain": (ctx.request.lead_domain or "").removesuffix("_agent") or None,
            },
        )
    ]


def _relative_width(run: ForecastRun) -> float | None:
    if len(run.interval) != 2 or not run.prediction:
        return None
    lo, hi = sorted(run.interval)
    return round(abs(hi - lo) / (2 * abs(float(run.prediction)) or 1.0), 4)


def _mape(run: ForecastRun) -> float | None:
    bt = run.backtest_metrics or {}
    for key in ("mape", "in_sample_mape", "MAPE"):
        v = bt.get(key)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None
