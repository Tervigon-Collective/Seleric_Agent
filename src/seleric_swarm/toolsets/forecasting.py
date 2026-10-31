"""ForecastToolset — governed multi-target forecasts for follow-ups.

``forecast_metrics`` compiles a plan straight from the metric ids and horizon
the model passes, then runs the forecast pipeline. Numbers come only from ``ForecastArtifact``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from pydantic_ai import RunContext

from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.forecast_plan import compile_forecast_plan
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.conversations.contracts import ArtifactProvenance
from seleric_swarm.forecasting.pipeline import run_forecast
from seleric_swarm.forecasting.policies import default_policies


def _refuse(summary: str, *, error_code: str = "INSUFFICIENT_EVIDENCE", retryable: bool = False) -> ToolResult:
    return ToolResult(success=False, summary=summary, error_code=error_code, retryable=retryable)


def _bare(metric_id: str) -> str:
    mid = (metric_id or "").strip()
    if mid.startswith("metric."):
        return mid[len("metric.") :]
    return mid


async def forecast_metrics(
    ctx: RunContext[SelericDeps],
    targets: list[str],
    horizon_days: int = 14,
    entity_dimension: str | None = None,
    entity_values: list[str] | None = None,
    combine_entities: bool = False,
    threshold: float | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
) -> ToolResult:
    """Forecast certified targets for ``horizon_days`` with optional entity slice.

    Follow-ups such as "and Meta only?" call this with the same targets and an
    ``entity_dimension`` / ``entity_values`` filter. Refuse rather than guess
    when a gate blocks. Set ``combine_entities`` to forecast several values as ONE
    summed series (a named group = all values matching it).
    For "will we reach/cross X" questions pass ``threshold`` as a plain number in the
    metric's unit (1 lakh = 100000, 1 crore = 10000000; subtract actuals already booked
    when the question is about a period that has started) and the skeleton reports the
    probability the horizon total reaches it.
    The forecast always starts today (as_of): for "tomorrow", "next week" or any window that
    does not start today, pass ``period_start`` / ``period_end`` too.
    For a named calendar period ("November", "rest of the month", "Q4") pass ISO
    ``period_start`` / ``period_end``: the forecast runs through ``period_end`` and the
    totals and threshold cover only that window (days already past are not forecast).
    """
    if not targets:
        return _refuse("forecast_metrics requires at least one target metric id")
    try:
        h = int(horizon_days)
    except (TypeError, ValueError):
        return _refuse("horizon_days must be an integer", error_code="INVALID_ARGUMENT")
    policies = default_policies()
    h = max(1, min(h, policies.defaults.max_horizon_days))

    bare_targets = [_bare(t) for t in targets if str(t).strip()]
    if not bare_targets:
        return _refuse("forecast_metrics requires at least one target metric id")

    as_of = _as_of(ctx)
    try:
        plan = await compile_forecast_plan(
            bare_targets,
            catalogue=ctx.deps.catalogue,
            as_of=as_of,
            horizon_days=h,
            entity_dimension=entity_dimension,
            entity_values=entity_values or (),
            combine_entities=combine_entities,
            threshold=threshold,
            period_start=period_start,
            period_end=period_end,
            policies=policies,
            timezone=getattr(getattr(ctx.deps, "context", None), "timezone", None) or "Asia/Kolkata",
            canonical=getattr(ctx.deps, "canonical_metric_id", None),
        )
    except Exception as exc:
        return _refuse(f"forecast plan failed: {type(exc).__name__}: {exc}")

    chronos_url = ""
    settings = getattr(getattr(ctx.deps, "context", None), "settings", None)
    if settings is not None:
        chronos_url = (getattr(settings, "chronos_base_url", "") or "").strip()

    try:
        outcome = await run_forecast(ctx.deps, plan, chronos_url=chronos_url or None)
    except Exception as exc:
        return _refuse(f"forecast pipeline failed: {type(exc).__name__}: {exc}")

    if outcome.refused or outcome.artifact is None:
        return ToolResult(
            success=False,
            summary=outcome.skeleton or "forecast refused",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
            warnings=list(outcome.warnings),
        )

    ids = [i for i in [outcome.artifact_id, *outcome.prediction_ids] if i]
    return ToolResult(
        success=True,
        artifact_ids=ids,
        summary=outcome.skeleton or f"forecast ready for {', '.join(bare_targets)}",
        provenance=ArtifactProvenance(
            evidence_ids=list(outcome.artifact.evidence_ids),
            calculation_version="forecast.pipeline.v2",
            model_version=f"{outcome.artifact.model_id}@{outcome.artifact.model_version}",
            source_metadata={
                "status": outcome.artifact.status,
                "input_hash": outcome.artifact.input_hash,
                "entity": outcome.artifact.entity,
            },
        ),
        warnings=list(outcome.warnings),
    )


def _as_of(ctx: RunContext[SelericDeps]) -> date:
    raw = getattr(ctx.deps, "as_of", None)
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    return datetime.now(UTC).date()
