"""Fallback ladder (spec sec. 30):

    registered production model  ->  approved statistical baseline  ->  INSUFFICIENT

`select_and_forecast` walks ``policies.fallback_order`` and returns the first
``ForecastRun`` it can produce, plus the list of reasons each earlier step was
skipped (for the audit / limitations). The LLM is never a fallback.
"""

from __future__ import annotations

from datetime import UTC, datetime

from seleric_swarm.agents.prediction.context import PredictionContext
from seleric_swarm.agents.prediction.contracts import ForecastRun
from seleric_swarm.agents.prediction.registries import ForecastModelQuery


async def select_and_forecast(ctx: PredictionContext) -> tuple[ForecastRun | None, list[str]]:
    reasons: list[str] = []
    for step in ctx.policies.fallback_order():
        if step == "registered_model":
            run = await _try_registered_model(ctx, reasons)
            if run is not None:
                return run, reasons
        elif step == "statistical_baseline":
            run = _try_baseline(ctx, reasons)
            if run is not None:
                return run, reasons
        elif step == "insufficient":
            break
    return None, reasons


async def _try_registered_model(ctx: PredictionContext, reasons: list[str]) -> ForecastRun | None:
    pol = ctx.policies
    reg = ctx.deps.model_registry

    # find a model whose target matches (registries may expose for_target/ids;
    # fall back to an explicit id on the request when they do not).
    candidate = None
    for_target = getattr(reg, "for_target", None)
    if callable(for_target):
        matches = for_target(ctx.target_metric)
        candidate = matches[0] if matches else None
    explicit = ctx.request.context.get("model_id")
    if explicit and reg.get(explicit):
        candidate = reg.get(explicit)

    if candidate is None:
        reasons.append(f"no registered model targets {ctx.target_metric}")
        return None
    if candidate.status.lower() not in pol.model_require_status():
        reasons.append(f"model '{candidate.model_id}' status '{candidate.status}' not in {sorted(pol.model_require_status())}")
        return None
    if pol.model_require_backtest() and not candidate.backtest_available:
        reasons.append(f"model '{candidate.model_id}' has no backtest metrics")
        return None
    if candidate.last_validated_at:
        age = _age_days(candidate.last_validated_at)
        if age is not None and age > pol.model_recent_validation_days():
            reasons.append(f"model '{candidate.model_id}' last validated {age}d ago")
            return None

    feature_set_id = None
    feature_set_version = None
    if pol.model_require_feature_set():
        fref = ctx.deps.feature_store.resolve(candidate.model_id)
        if fref is None:
            reasons.append(f"no feature set registered for model '{candidate.model_id}'")
            return None
        if fref.missing_features:
            reasons.append(f"feature set '{fref.feature_set_id}' missing {fref.missing_features}")
            return None
        feature_set_id, feature_set_version = fref.feature_set_id, fref.version

    run = await ctx.deps.model_service.forecast(
        ForecastModelQuery(
            target_metric=ctx.target_metric,
            horizon=ctx.horizon,
            model_id=candidate.model_id,
            model_version=candidate.version,
            features={"trend_pct": ctx.trend_pct, "cause_persistence": ctx.cause_persistence},
            history=ctx.history or None,
            observations=ctx.request.observations,
        )
    )
    if run is None:
        reasons.append(f"model service returned no forecast for {ctx.target_metric}")
        return None

    run.model_id = run.model_id or candidate.model_id
    run.model_version = run.model_version or candidate.version
    run.feature_set_id = run.feature_set_id or feature_set_id
    run.feature_set_version = run.feature_set_version or feature_set_version
    if run.drift_status is None:
        run.drift_status = ctx.drift_status
    return run


def _try_baseline(ctx: PredictionContext, reasons: list[str]) -> ForecastRun | None:
    pol = ctx.policies
    if not pol.baseline_approved():
        reasons.append("statistical baseline is not approved by policy")
        return None
    if len(ctx.history) < pol.baseline_min_history():
        reasons.append(
            f"insufficient history for baseline ({len(ctx.history)} < {pol.baseline_min_history()} points)"
        )
        return None
    run = ctx.deps.baseline.forecast(
        target_metric=ctx.target_metric, horizon=ctx.horizon, history=ctx.history
    )
    if run is None:
        reasons.append("baseline forecaster returned nothing")
        return None
    if run.drift_status is None:
        run.drift_status = ctx.drift_status or "n/a"
    return run


def _age_days(iso: str) -> int | None:
    normalized = f"{iso[:-1]}+00:00" if iso.endswith("Z") else iso
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=UTC)
    return (datetime.now(UTC) - dt).days
