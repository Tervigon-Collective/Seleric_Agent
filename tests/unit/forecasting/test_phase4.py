"""Phase 4: candidate bundles, calibrated totals, explain, golden questions."""

from __future__ import annotations

from seleric_swarm.evals.golden_dataset import load_golden_dataset
from seleric_swarm.forecasting.__main__ import explain_forecast
from seleric_swarm.forecasting.derive import horizon_totals
from seleric_swarm.forecasting.policies import clear_policy_cache, load_forecast_policies
from seleric_swarm.forecasting.types import DailyPoint
from seleric_swarm.paths import repo_root


def test_candidate_bundles_carry_backtest_quantiles():
    clear_policy_cache()
    policies = load_forecast_policies()
    for mid, ape in (("net_sales", -0.316), ("orders", -0.364)):
        bundle = policies.target(mid).scored_bundle()
        assert bundle is not None
        assert bundle.status == "candidate"
        assert bundle.engine == "chronos-2"
        assert policies.target(mid).approved_bundle() is None
        assert bundle.backtest.total_error_quantiles["h14"][0] == ape


def test_h14_calibration_uses_bundle_band():
    days = [DailyPoint(date=f"2026-10-{i:02d}", mean=10, p10=8, p50=10, p90=12) for i in range(1, 15)]
    mean, p10, p90, warnings = horizon_totals(
        days,
        total_error_quantiles={"h14": [-0.316, 0.316]},
        horizon_key="h14",
    )
    assert mean == 140
    assert abs(p10 - 140 * 0.684) < 1e-6
    assert abs(p90 - 140 * 1.316) < 1e-6
    assert warnings == []


def test_explain_backtest_report():
    path = repo_root() / "reports" / "forecasting" / "2026-10-10" / "net_sales.json"
    explained = explain_forecast(str(path))
    assert explained["found"] is True
    assert explained["engine"] == "chronos-2"
    assert "chronos-2" in explained["backtest_summary"]


def test_forecast_golden_questions_present():
    cases = {c.id: c for c in load_golden_dataset()}
    expected = {
        "forecast-net-sales-14d",
        "forecast-orders-by-platform",
        "forecast-aov-next-week",
        "forecast-sparse-sku",
        "forecast-refunds",
    }
    assert expected <= set(cases)
    assert cases["forecast-aov-next-week"].expected["derived"] is True
    assert cases["forecast-sparse-sku"].expected["refusal"] == "Q_SPARSE_ENTITY"
    assert cases["forecast-refunds"].expected["reason"] == "T_PROVISIONAL"
