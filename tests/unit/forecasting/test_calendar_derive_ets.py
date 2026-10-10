"""Calendar features, derive totals, and ETS forecast_path."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from seleric_swarm.forecasting.calendar import generate_calendar_features, load_calendar_config
from seleric_swarm.forecasting.derive import horizon_totals, ratio_from_components
from seleric_swarm.forecasting.types import DailyPoint, TargetForecast
from seleric_swarm.models.service import ForecastUnavailable, forecast_path, forecast_series


def test_calendar_flags_diwali_2026():
    cfg = load_calendar_config()
    diwali = date(2026, 11, 8)
    feats = generate_calendar_features([diwali], cfg=cfg)
    assert feats["calendar.festival"] == [1.0]
    assert feats["calendar.dow"][0] == float(diwali.weekday())


def test_calendar_payday_and_month_end():
    d = date(2026, 10, 31)
    feats = generate_calendar_features([d, date(2026, 11, 1)])
    assert feats["calendar.month_end"][0] == 1.0
    assert feats["calendar.payday"][0] == 1.0
    assert feats["calendar.payday"][1] == 1.0


def test_horizon_totals_comonotonic_warns():
    days = [
        DailyPoint(date="2026-10-01", mean=10, p10=8, p50=10, p90=12),
        DailyPoint(date="2026-10-02", mean=20, p10=15, p50=20, p90=25),
    ]
    mean, p10, p90, warnings = horizon_totals(days)
    assert mean == 30
    assert p10 == 23 and p90 == 37
    assert "comonotonic_total_interval" in warnings


def test_horizon_totals_calibrated():
    days = [DailyPoint(date="2026-10-01", mean=100, p10=80, p50=100, p90=120)]
    mean, p10, p90, warnings = horizon_totals(
        days, total_error_quantiles={"h1": [-0.1, 0.2]}, horizon_key="h1"
    )
    assert mean == 100
    assert p10 == pytest.approx(90)
    assert p90 == pytest.approx(120)
    assert warnings == []


def test_ratio_recomposition():
    sales = TargetForecast(
        metric_id="net_sales",
        status="provisional",
        days=[DailyPoint(date="2026-10-01", mean=1000, p10=900, p50=1000, p90=1100)],
        total_mean=1000,
    )
    orders = TargetForecast(
        metric_id="orders",
        status="provisional",
        days=[DailyPoint(date="2026-10-01", mean=10, p10=8, p50=10, p90=12)],
        total_mean=10,
    )
    aov = ratio_from_components(sales, orders, metric_id="aov", label="AOV")
    assert aov.days[0].mean == pytest.approx(100.0)
    assert aov.total_mean == pytest.approx(100.0)
    assert "approximate_ratio_interval" in aov.reason_codes


def test_forecast_path_weekly_seasonality():
    # 35 days of weekly pattern
    values = [10.0 + (i % 7) for i in range(35)]
    path = forecast_path(values, horizon_days=7, model_id="forecast.test", nonnegative=True)
    assert len(path.points) == 7
    assert len(path.lows) == 7 and len(path.highs) == 7
    assert "weekly" in path.method or path.method.startswith("holt")
    assert all(p >= 0 for p in path.points)
    assert all(lo <= hi for lo, hi in zip(path.lows, path.highs, strict=True))


def test_forecast_series_uses_path_final_step():
    values = [float(i) for i in range(20)]

    class _E:
        value = None
        period_start = None

    # Build fake evidence-like objects
    from types import SimpleNamespace
    from datetime import datetime, UTC

    evidence = [
        SimpleNamespace(value=v, period_start=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=i))
        for i, v in enumerate(values)
    ]
    result = forecast_series(evidence, horizon_days=3, model_id="forecast.test")  # type: ignore[arg-type]
    assert result.horizon_days == 3
    assert result.interval[0] <= result.value <= result.interval[1]


def test_forecast_path_refuses_thin_history():
    with pytest.raises(ForecastUnavailable):
        forecast_path([1.0, 2.0], horizon_days=3, model_id="x")
