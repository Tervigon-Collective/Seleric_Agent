"""Forecast backend abstraction: selection, guards, refusal, determinism."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.models.backends import BackendForecast, get_backend, register_backend
from seleric_swarm.models.service import (
    ForecastUnavailable,
    ModelRecord,
    YamlModelRegistry,
    forecast_series,
)
from seleric_swarm.toolsets import policy_config as policy


def _series(n: int = 40) -> list[EvidenceArtifact]:
    out = []
    for i in range(n):
        stamp = datetime(2026, 8, 1, tzinfo=UTC) + timedelta(days=i)
        weekly = 10.0 if i % 7 in (5, 6) else 0.0
        out.append(
            EvidenceArtifact(
                metric_id="metric.net_sales",
                grain="day",
                as_of=datetime(2026, 9, 20, tzinfo=UTC),
                period_start=stamp,
                period_end=stamp,
                value=100.0 + i * 0.5 + weekly + (i % 3),
                source_query={"measure": "metric.net_sales"},
            )
        )
    return out


def test_default_backend_is_holt_and_deterministic():
    a = forecast_series(_series(), horizon_days=7, model_id="m")
    b = forecast_series(_series(), horizon_days=7, model_id="m")
    assert a == b
    assert a.method.startswith(("holt", "simple"))
    assert a.interval[0] <= a.value <= a.interval[1]


def test_unknown_backend_refuses():
    with pytest.raises(ForecastUnavailable) as exc:
        forecast_series(_series(), horizon_days=7, model_id="m", backend="nope")
    assert exc.value.warning == policy.WARN_MODEL_UNAVAILABLE


def test_statsforecast_backend_forecasts_or_refuses_cleanly():
    try:
        result = forecast_series(_series(), horizon_days=7, model_id="m", backend="statsforecast")
    except ForecastUnavailable as exc:
        # optional dependency not installed -> named policy refusal, never a crash
        assert exc.warning == policy.WARN_MODEL_UNAVAILABLE
        return
    assert result.interval[0] <= result.value <= result.interval[1]
    assert result.method.startswith("auto_ets")


def test_shared_guards_apply_to_every_backend():
    class Bad:
        name = "bad"

        def forecast(self, values, horizon_days, *, season_length=None):
            return BackendForecast(point=float("nan"), lower=0.0, upper=1.0, method="bad")

    register_backend(Bad())
    with pytest.raises(ForecastUnavailable) as exc:
        forecast_series(_series(), horizon_days=7, model_id="m", backend="bad")
    assert exc.value.warning == policy.WARN_MODEL_FIT_FAILED
    with pytest.raises(ForecastUnavailable):
        forecast_series(_series(), horizon_days=0, model_id="m", backend="holt")


def test_registry_parses_backend_and_fallback(tmp_path):
    p = tmp_path / "reg.yaml"
    p.write_text(
        "models:\n"
        "  - {id: a, target: t, status: approved, backend: statsforecast, fallback: b}\n"
        "  - {id: b, target: t, status: approved}\n",
        encoding="utf-8",
    )
    reg = YamlModelRegistry(p)
    assert reg.get("a").backend == "statsforecast"
    assert reg.get("a").fallback == "b"
    assert reg.get("b").backend == "holt"
    assert reg.get("b").fallback is None


def test_candidate_ets_entries_are_not_approved():
    reg = YamlModelRegistry()
    ets = [r for r in (reg.get(i) for i in reg.ids()) if r and r.backend == "statsforecast"]
    assert ets and all(isinstance(r, ModelRecord) and r.status == "candidate" for r in ets)
    assert get_backend("holt").name == "holt"


@pytest.mark.asyncio
async def test_toolset_falls_back_when_backend_unavailable_and_records_actual_model(monkeypatch):
    from seleric_swarm.models.service import InMemoryModelRegistry
    from seleric_swarm.state.artifacts import InMemoryArtifactStore
    from seleric_swarm.toolsets import models
    from tests.unit.test_models_toolset import FakeRunContext, _deps, _store_series

    reg = InMemoryModelRegistry(
        {
            "primary": ModelRecord(
                model_id="primary", status="approved", target="metric.net_sales",
                backend="does_not_exist", fallback="base", minimum_history_days=8,
            ),
            "base": ModelRecord(
                model_id="base", status="approved", target="metric.net_sales",
                minimum_history_days=8,
            ),
        }
    )
    monkeypatch.setitem(models._registry_cache, "r", reg)

    store = InMemoryArtifactStore()
    ids = _store_series(store)
    result = await models.forecast(FakeRunContext(_deps(store)), ids, horizon_days=3)

    assert result.success, result.summary
    assert store.get(result.artifact_ids[0]).payload["model_id"] == "base"
    assert any("fallback model base" in w for w in result.warnings)


def test_conformal_wrapper_keeps_point_and_calibrates_interval():
    plain = forecast_series(_series(60), horizon_days=7, model_id="m", backend="holt")
    conf = forecast_series(_series(60), horizon_days=7, model_id="m", backend="holt+conformal")
    assert conf.value == plain.value
    assert conf.method.endswith("+conformal")
    assert conf.interval[0] <= conf.value <= conf.interval[1]
    assert conf.interval != plain.interval


def test_conformal_wrapper_declines_to_calibrate_on_short_history():
    res = forecast_series(_series(14), horizon_days=7, model_id="m", backend="holt+conformal")
    assert not res.method.endswith("+conformal")
    assert any("calibration skipped" in w for w in res.warnings)


def test_unknown_inner_backend_with_conformal_suffix_refuses():
    with pytest.raises(ForecastUnavailable):
        get_backend("nope+conformal")
