"""ForecastToolset — governed multi-target forecasts for follow-ups.

``forecast_metrics`` reuses the same compiler and pipeline as the runner
prefetch path, so "and Meta only?" / a different horizon does not invent a
parallel code path. Numbers come only from ``ForecastArtifact``.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from pydantic_ai import RunContext

from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.forecast_plan import compile_forecast_plan
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.agent.plan import MetricSlot
from seleric_swarm.agent.understand import ForecastHorizonSlot, ForecastSlots, Understanding
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
) -> ToolResult:
    """Forecast certified targets for ``horizon_days`` with optional entity slice.

    Follow-ups such as "and Meta only?" call this with the same targets and an
    ``entity_dimension`` / ``entity_values`` filter. The pipeline is identical
    to the mission prefetch path — refuse rather than guess when gates block.
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
    slots = ForecastSlots(
        targets=[MetricSlot(words=mid, metric_id=mid) for mid in bare_targets],
        horizon=ForecastHorizonSlot(n=h, unit="day", period_word="next_n"),
    )
    understanding = Understanding.model_validate(
        {
            "kind": "forecast",
            "shape": "lookup",
            "entity_dimension": (entity_dimension or "").strip(),
            "rank_by": None,
            "metrics": [MetricSlot(words=mid, metric_id=mid) for mid in bare_targets],
            "breakdown_dimensions": [],
            "names_period": False,
            "forecast": slots,
        }
    )

    scope = _scoped(ctx, entity_dimension, entity_values)
    try:
        plan = await compile_forecast_plan(
            understanding,
            catalogue=ctx.deps.catalogue,
            as_of=as_of,
            scope=scope,
            policies=policies,
            timezone=getattr(getattr(ctx.deps, "context", None), "timezone", None) or "Asia/Kolkata",
            canonical=getattr(ctx.deps, "canonical_metric_id", None),
        )
    except Exception as exc:  # noqa: BLE001
        return _refuse(f"forecast plan failed: {type(exc).__name__}: {exc}")

    if entity_values and plan.entity_dimension and not plan.entity_values:
        plan = plan.model_copy(update={"entity_values": [str(v) for v in entity_values]})

    chronos_url = ""
    settings = getattr(getattr(ctx.deps, "context", None), "settings", None)
    if settings is not None:
        chronos_url = (getattr(settings, "chronos_base_url", "") or "").strip()

    try:
        outcome = await run_forecast(ctx.deps, plan, chronos_url=chronos_url or None)
    except Exception as exc:  # noqa: BLE001
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
    return datetime.now(timezone.utc).date()


def _scoped(
    ctx: RunContext[SelericDeps],
    entity_dimension: str | None,
    entity_values: list[str] | None,
) -> Any:
    """Prefer the mission scope; overlay explicit entity filters from the tool call."""
    scope = getattr(ctx.deps, "required_scope", None)
    if not entity_dimension or not entity_values:
        return scope
    values = [str(v) for v in entity_values if str(v).strip()]
    if not values:
        return scope

    class _Filter:
        def __init__(self, dimension: str, vals: list[str]) -> None:
            self.dimensions = [dimension]
            self.values = vals
            self.term = vals[0] if vals else ""

    class _Scope:
        def __init__(self, base: Any, dimension: str, vals: list[str]) -> None:
            base_filters = list(getattr(base, "value_filters", None) or []) if base is not None else []
            self.value_filters = [*base_filters, _Filter(dimension, vals)]

    return _Scope(scope, entity_dimension.strip(), values)
