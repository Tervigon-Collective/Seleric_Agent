"""Backtest helpers and evaluation primitives."""

from __future__ import annotations

from datetime import date

from seleric_swarm.forecasting.backtest import propose_bundle_yaml, rolling_cutoffs, score_fold
from seleric_swarm.models.evaluation import coverage, horizon_total_ape, mase, wql


def test_rolling_cutoffs_weekly():
    cuts = rolling_cutoffs(end=date(2026, 10, 1), lookback_days=28, step_days=7)
    assert len(cuts) == 5
    assert cuts[0] == date(2026, 9, 3)
    assert cuts[-1] == date(2026, 10, 1)


def test_score_fold_and_eval_primitives():
    actuals = [10.0, 12.0, 11.0, 13.0]
    p50 = [11.0, 11.0, 11.0, 12.0]
    p10 = [9.0, 9.0, 9.0, 10.0]
    p90 = [13.0, 13.0, 13.0, 14.0]
    metrics = score_fold(actuals, p10, p50, p90, insample=[10] * 20)
    assert "wql" in metrics and "coverage_80" in metrics
    assert coverage(actuals, p10, p90) == 1.0
    assert horizon_total_ape(sum(actuals), sum(p50)) is not None
    assert wql(actuals, {0.5: p50}) >= 0
    # MASE with constant insample seasonal diffs → nan scale avoided by variation
    assert mase([10, 11, 12, 13, 14, 15, 16, 17], [10] * 8, insample=list(range(20))) >= 0


def test_propose_bundle_yaml():
    text = propose_bundle_yaml(
        target="net_sales",
        engine="chronos-2",
        co_targets=["orders"],
        past_covariates=["ad_spend"],
        known_future=["calendar.dow"],
        metrics={"wql": 0.1, "mase": 0.9, "coverage_80": 0.8},
        report_path="reports/forecasting/2026-10-09/net_sales.json",
    )
    assert "net_sales.v1" in text and "chronos-2" in text
