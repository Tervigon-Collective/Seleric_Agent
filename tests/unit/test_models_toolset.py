"""ModelToolset + forecasting service + prediction→actual loop (Sprint 3, C).

The central property under test is that **the registry is a real control**, not
decoration: a metric with an approved model gets a forecast, one without gets a
refusal, and no code path invents a number for an unbacked target. That is what
makes `predict_ltv`/`predict_propensity` refusing correct behavior rather than
an unfinished implementation.

Offline: no `runtime` fixture, no MCP, no LLM. `statsmodels` is a declared
dependency, so the forecast path runs for real rather than against a stub.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from seleric_swarm.agent.artifacts import EvidenceArtifact, PredictionArtifact
from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.conversations.contracts import (
    Artifact,
    ArtifactProvenance,
    ContextBundle,
    Principal,
    PrincipalAuthMethod,
)
from seleric_swarm.models.evaluation import (
    evaluate_prediction,
    pair_predictions_with_actuals,
    summarize,
)
from seleric_swarm.models.service import ForecastUnavailable, forecast_series
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import models

_MISSION = "MS3-models"
_APPROVED_METRIC = "metric.net_sales"
_UNBACKED_METRIC = "metric.cpm"  # real catalogue id, deliberately not in the registry


class FakeRunContext:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _deps(store: InMemoryArtifactStore) -> SelericDeps:
    return SelericDeps(
        mission_id=_MISSION,
        as_of=datetime(2026, 9, 20, tzinfo=UTC),
        principal=Principal(
            principal_id="p1",
            workspace_id="ws-1",
            user_id="user-1",
            authenticated=False,
            auth_method=PrincipalAuthMethod.ANONYMOUS,
        ),
        thread_id="thread-1",
        run_id="run-1",
        trace_id="trace-1",
        context=ContextBundle(),
        mcp_client=NullMcpClient(),
        artifact_store=store,
        limits=ExecutionLimits(),
    )


def _ev(day: int, value: float | None, *, metric: str = _APPROVED_METRIC) -> EvidenceArtifact:
    stamp = datetime(2026, 9, 1, tzinfo=UTC) + timedelta(days=day - 1)
    return EvidenceArtifact(
        metric_id=metric,
        grain="day",
        as_of=datetime(2026, 9, 20, tzinfo=UTC),
        period_start=stamp,
        period_end=stamp,
        value=value,
        source_query={"measure": metric},
    )


def _store_series(
    store: InMemoryArtifactStore, *, days: int = 14, metric: str = _APPROVED_METRIC, slope: float = 3.0
) -> list[str]:
    ids: list[str] = []
    for day in range(1, days + 1):
        payload = _ev(day, 100.0 + slope * day, metric=metric)
        artifact = store.put(
            Artifact(
                workspace_id="ws-1",
                artifact_type="evidence",
                payload=payload.model_dump(mode="json"),
                classification="factual",
                evidence_ids=[f"raw:{metric}:{day}"],
                provenance=ArtifactProvenance(query_version="q1"),
                mission_id=_MISSION,
            )
        )
        ids.append(artifact.id)
    return ids


# ---- the registry as a control ----------------------------------------------


@pytest.mark.asyncio
async def test_forecast_succeeds_for_a_metric_with_an_approved_model():
    store = InMemoryArtifactStore()
    ids = _store_series(store)
    ctx = FakeRunContext(_deps(store))

    result = await models.forecast(ctx, ids, horizon_days=3)

    assert result.success, result.summary
    payload = store.get(result.artifact_ids[0]).payload
    assert payload["model_id"] == "forecast.net_sales.daily"
    assert payload["model_version"] == "1"  # rule 10
    assert payload["confidence_interval"] is not None  # never a bare point
    assert payload["feature_leakage_checked"] is True  # rule 20, explicit


@pytest.mark.asyncio
async def test_forecast_refuses_a_metric_with_no_approved_model():
    """The registry gate. metric.cpm is a real catalogue id with no registered
    model — the answer is a refusal, not a number from a generic fallback."""
    store = InMemoryArtifactStore()
    ids = _store_series(store, metric=_UNBACKED_METRIC)
    ctx = FakeRunContext(_deps(store))

    result = await models.forecast(ctx, ids, horizon_days=3)

    assert result.success is False
    assert result.error_code == "INSUFFICIENT_EVIDENCE"
    assert "policy:no_approved_model" in result.warnings
    assert result.artifact_ids == []


@pytest.mark.asyncio
async def test_predict_ltv_refuses_because_no_model_is_registered():
    """Not an unimplemented stub — the registry has no LTV entry because this
    deployment has no per-customer training data. Refusing IS the behavior."""
    result = await models.predict_ltv(FakeRunContext(_deps(InMemoryArtifactStore())), [])
    assert result.success is False
    assert "policy:no_approved_model" in result.warnings


@pytest.mark.asyncio
async def test_predict_propensity_refuses_because_no_model_is_registered():
    result = await models.predict_propensity(
        FakeRunContext(_deps(InMemoryArtifactStore())), [], event="purchase"
    )
    assert result.success is False
    assert "policy:no_approved_model" in result.warnings


# ---- preconditions ----------------------------------------------------------


@pytest.mark.asyncio
async def test_forecast_refuses_a_mixed_metric_evidence_set():
    store = InMemoryArtifactStore()
    ids = _store_series(store, days=10) + _store_series(store, days=10, metric=_UNBACKED_METRIC)
    result = await models.forecast(FakeRunContext(_deps(store)), ids, horizon_days=3)

    assert result.success is False
    assert "policy:model_target_mismatch" in result.warnings


@pytest.mark.asyncio
async def test_forecast_refuses_a_window_aggregate_labelled_day_grain():
    """A1.2 applies here for the same reason it applies to detect_anomalies: a
    5-day sum wearing a daily label would silently become a history point
    (docs/BUG_SHEET.md #14)."""
    store = InMemoryArtifactStore()
    ids = _store_series(store, days=10)
    aggregate = EvidenceArtifact(
        metric_id=_APPROVED_METRIC,
        grain="day",
        as_of=datetime(2026, 9, 20, tzinfo=UTC),
        period_start=datetime(2026, 9, 11, tzinfo=UTC),
        period_end=datetime(2026, 9, 15, tzinfo=UTC),
        value=5000.0,
        source_query={},
    )
    ids.append(
        store.put(
            Artifact(
                workspace_id="ws-1",
                artifact_type="evidence",
                payload=aggregate.model_dump(mode="json"),
                classification="factual",
                evidence_ids=["raw:agg"],
                provenance=ArtifactProvenance(query_version="q1"),
                mission_id=_MISSION,
            )
        ).id
    )

    result = await models.forecast(FakeRunContext(_deps(store)), ids, horizon_days=3)

    assert result.success is False
    assert result.error_code == "EVIDENCE_GRAIN_MISMATCH"


@pytest.mark.asyncio
async def test_forecast_refuses_history_below_the_registered_minimum():
    store = InMemoryArtifactStore()
    ids = _store_series(store, days=4)  # registry minimum_history_days is 8
    result = await models.forecast(FakeRunContext(_deps(store)), ids, horizon_days=3)

    assert result.success is False
    assert "policy:thin_history" in result.warnings


@pytest.mark.asyncio
async def test_forecast_refuses_a_nonsense_horizon():
    store = InMemoryArtifactStore()
    ids = _store_series(store)
    result = await models.forecast(FakeRunContext(_deps(store)), ids, horizon_days=0)

    assert result.success is False
    assert "policy:invalid_horizon" in result.warnings


# ---- the forecasting service itself -----------------------------------------


def test_forecast_follows_a_rising_trend():
    """100 + 3d over 14 days, forecast 3 ahead of day 14 -> day 17 -> 151."""
    series = [_ev(d, 100.0 + 3.0 * d) for d in range(1, 15)]
    result = forecast_series(series, horizon_days=3, model_id="forecast.net_sales.daily")

    assert result.method == "holt_linear_trend"
    assert result.value == pytest.approx(151.0, abs=1.0)
    assert result.history_points == 14


def test_short_series_smooths_the_level_instead_of_fitting_a_trend():
    """Below 10 points a trend estimate is noise amplification, so the method
    degrades deliberately rather than over-fitting."""
    series = [_ev(d, 100.0 + d) for d in range(1, 10)]
    result = forecast_series(series, horizon_days=1, model_id="forecast.net_sales.daily")
    assert result.method == "simple_exponential_smoothing"


def test_flat_fit_warns_instead_of_implying_certainty():
    """A zero-width interval on a perfectly linear series is arithmetically
    right but reads as false precision, so it says so."""
    series = [_ev(d, 100.0 + 3.0 * d) for d in range(1, 15)]
    result = forecast_series(series, horizon_days=1, model_id="forecast.net_sales.daily")
    if result.interval[0] == result.interval[1]:
        assert any("collapsed to a point" in w for w in result.warnings)


def test_noisy_series_produces_a_widening_interval():
    """Uncertainty compounds with horizon — a 7-day-ahead interval must be
    wider than a 1-day-ahead one."""
    values = [100.0, 118.0, 95.0, 130.0, 102.0, 88.0, 141.0, 99.0, 120.0, 108.0, 133.0, 91.0]
    series = [_ev(d, v) for d, v in enumerate(values, start=1)]
    near = forecast_series(series, horizon_days=1, model_id="forecast.net_sales.daily")
    far = forecast_series(series, horizon_days=7, model_id="forecast.net_sales.daily")

    assert (far.interval[1] - far.interval[0]) > (near.interval[1] - near.interval[0])


def test_null_values_are_dropped_not_treated_as_zero():
    series = [_ev(d, 100.0 + d) for d in range(1, 12)] + [_ev(12, None)]
    result = forecast_series(series, horizon_days=1, model_id="forecast.net_sales.daily")
    assert result.history_points == 11


def test_thin_history_raises_rather_than_extrapolating():
    with pytest.raises(ForecastUnavailable) as excinfo:
        forecast_series([_ev(1, 100.0), _ev(2, 101.0)], horizon_days=1, model_id="m")
    assert excinfo.value.warning == "policy:thin_history"


# ---- prediction -> actual feedback loop -------------------------------------


def _prediction(value: float, interval: tuple[float, float] | None) -> PredictionArtifact:
    return PredictionArtifact(
        model_id="forecast.net_sales.daily",
        model_version="1",
        prediction_type="forecast",
        value=value,
        confidence_interval=interval,
        evidence_ids=["ev-1"],
        feature_leakage_checked=True,
    )


def test_evaluate_prediction_scores_error_and_interval_coverage():
    scored = evaluate_prediction(_prediction(100.0, (90.0, 110.0)), _ev(1, 105.0))

    assert scored is not None
    assert scored.absolute_error == pytest.approx(5.0)
    assert scored.percentage_error == pytest.approx(5.0 / 105.0 * 100, abs=1e-3)
    assert scored.within_interval is True
    assert scored.interval_missed is False


def test_actual_outside_the_interval_is_a_miss():
    scored = evaluate_prediction(_prediction(100.0, (90.0, 110.0)), _ev(1, 200.0))
    assert scored is not None and scored.interval_missed is True


def test_missing_interval_is_unanswerable_not_a_miss():
    """Collapsing 'no interval' into 'missed' would flatter a model that never
    stated its uncertainty."""
    scored = evaluate_prediction(_prediction(100.0, None), _ev(1, 200.0))
    assert scored is not None
    assert scored.within_interval is None
    assert scored.interval_missed is False


def test_null_actual_cannot_be_scored():
    """Returning a zero error for an unmeasured outcome would be a lie."""
    assert evaluate_prediction(_prediction(100.0, (90.0, 110.0)), _ev(1, None)) is None


def test_pairing_matches_on_metric_and_period_not_model_name():
    target = datetime(2026, 9, 5, tzinfo=UTC)
    actuals = [_ev(5, 105.0), _ev(6, 900.0)]
    errors = pair_predictions_with_actuals(
        [(_prediction(100.0, (90.0, 110.0)), _APPROVED_METRIC, target)], actuals
    )

    assert len(errors) == 1
    assert errors[0].actual == 105.0


def test_unmeasured_prediction_is_skipped_silently():
    """The normal state of a forecast is 'not yet measured'."""
    future = datetime(2027, 1, 1, tzinfo=UTC)
    errors = pair_predictions_with_actuals(
        [(_prediction(100.0, None), _APPROVED_METRIC, future)], [_ev(1, 100.0)]
    )
    assert errors == []


def test_summarize_reports_mae_mape_and_coverage():
    errors = [
        evaluate_prediction(_prediction(100.0, (90.0, 110.0)), _ev(1, 105.0)),
        evaluate_prediction(_prediction(100.0, (90.0, 110.0)), _ev(2, 200.0)),
    ]
    summary = summarize([e for e in errors if e is not None])

    assert summary["count"] == 2.0
    assert summary["mae"] == pytest.approx((5.0 + 100.0) / 2)
    assert summary["interval_coverage"] == pytest.approx(0.5)


def test_summarize_of_nothing_is_empty_not_zero():
    assert summarize([]) == {}
