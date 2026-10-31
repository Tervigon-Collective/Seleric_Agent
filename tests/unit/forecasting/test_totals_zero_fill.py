"""Horizon-total bands and absent-day handling for sliced series."""

from __future__ import annotations

from seleric_swarm.forecasting.assembler import _fill_absent_days
from seleric_swarm.forecasting.derive import horizon_totals
from seleric_swarm.forecasting.types import DailyPoint
from seleric_swarm.models.service import forecast_path


def test_total_band_tightens_with_horizon_unlike_comonotonic_sum():
    values = [100.0 + (i % 7) * 10 + (15 if i % 3 else -15) for i in range(200)]
    res = forecast_path(values, horizon_days=30, model_id="t", nonnegative=True, interval_z=1.2816)
    days = [DailyPoint(date=str(i), mean=m, p10=lo, p50=m, p90=hi)
            for i, (m, lo, hi) in enumerate(zip(res.points, res.lows, res.highs))]
    mean, lo, hi, _ = horizon_totals(days, total_sd=res.total_sd(0, 29))
    _, c_lo, c_hi, w = horizon_totals(days)
    assert "comonotonic_total_interval" in w
    assert c_lo <= lo < mean < hi <= c_hi
    assert (hi - lo) / mean < 0.5 * (c_hi - c_lo) / mean


def test_absent_days_after_first_sale_are_zero():
    assert _fill_absent_days([None, 3.0, None, None, 5.0, None], True) == [None, 3.0, 0.0, 0.0, 5.0, 0.0]
    assert _fill_absent_days([None, 3.0, None], False) == [None, 3.0, None]


def test_total_interval_borrows_nearest_backtested_horizon():
    days = [DailyPoint(date=str(i), mean=100.0, p10=0.0, p50=100.0, p90=200.0) for i in range(28)]
    mean, lo, hi, w = horizon_totals(days, total_error_quantiles={"h14": [-0.3, 0.3]})
    assert (lo, hi) == (mean * 0.7, mean * 1.3) and "total_interval_scaled_from_h14" in w
    short = days[:7]
    _, _, hi7, _ = horizon_totals(short, total_error_quantiles={"h14": [-0.3, 0.3]})
    assert hi7 / 700 - 1 > 0.3  # shorter horizon is wider than the measured h14 error
    _, lo1, _, _ = horizon_totals(days[:1], total_error_quantiles={"h14": [-0.3, 0.3]})
    assert lo1 == 0.0 or lo1 > 0  # widening never takes a non-negative total below zero


def test_absent_day_is_zero_only_when_the_warehouse_was_alive():
    from datetime import date, timedelta

    from seleric_swarm.forecasting.assembler import _fill_alive_absent

    idx = [date(2026, 1, 1) + timedelta(days=i) for i in range(6)]
    alive = {idx[i].isoformat() for i in (1, 2, 4, 5)}  # day 3 is a warehouse-wide gap
    vals = [5.0, None, 3.0, None, None, None]
    assert _fill_alive_absent(vals, idx, alive) == [5.0, 0.0, 3.0, None, 0.0, 0.0]


def test_empirical_total_error_sizes_band_from_realised_misses():
    import random

    from seleric_swarm.forecasting.calibration import empirical_total_error

    rng = random.Random(7)
    clean = [100 + 20 * (i % 7 == 5) + rng.gauss(0, 15) for i in range(400)]
    lo, hi, windows = empirical_total_error(clean, lead=1, first=0, n=14, nonnegative=True)
    assert windows >= 12 and lo < 0 < hi and hi - lo < 0.5
    assert empirical_total_error(clean[:150], lead=1, first=0, n=14, nonnegative=True) is None
    _, p10, p90, w = horizon_totals(
        [DailyPoint(date=str(i), mean=100.0, p10=0.0, p50=100.0, p90=200.0) for i in range(14)],
        empirical=(lo, hi, windows),
    )
    assert p10 == 1400 * (1 + lo) and p90 == 1400 * (1 + hi) and w[0].startswith("total_interval_backtested")
