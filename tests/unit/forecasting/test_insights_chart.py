import math
from datetime import date, timedelta

from seleric_swarm.forecasting.chart import build_forecast_chart_spec
from seleric_swarm.forecasting.insights import build_insight_facts, render_insight_facts
from seleric_swarm.forecasting.types import DailyPoint
from seleric_swarm.models.service import Z_80, forecast_path


def _setup():
    h = [100 + 20 * math.sin(i / 7 * 2 * math.pi) + (i % 5) for i in range(200)]
    hd = [date(2026, 1, 1) + timedelta(i) for i in range(200)]
    r = forecast_path(h, horizon_days=14, model_id="m", nonnegative=True, interval_z=Z_80)
    st = hd[-1] + timedelta(1)
    days = [
        DailyPoint(date=(st + timedelta(i)).isoformat(), mean=p, p10=lo, p50=p, p90=hi)
        for i, (p, lo, hi) in enumerate(zip(r.points, r.lows, r.highs, strict=True))
    ]
    return h, hd, days


def test_band_is_tight_for_stable_series_and_ordered():
    _, _, days = _setup()
    assert all(d.p10 <= d.p50 <= d.p90 for d in days)
    assert (days[-1].p90 - days[-1].p10) / days[-1].p50 < 0.5


def test_insight_facts_are_generic_and_rendered():
    h, hd, days = _setup()
    facts = build_insight_facts(history=h, history_dates=hd, days=days)
    assert "weekday_peak" in facts and "level_vs_recent_pct" in facts
    text = "\n".join(render_insight_facts("anything", facts))
    assert "forecast daily average" in text and "weekly rhythm" in text


def test_chart_spec_has_history_forecast_and_band():
    h, hd, days = _setup()
    spec = build_forecast_chart_spec(
        metric_id="x", label=None, unit=None, history=h, history_dates=hd, days=days
    )
    assert spec["chart_type"] == "line"
    keys = {s["key"] for s in spec["series"]}
    assert {"Actual", "Forecast", "Lower (P10)", "Range width (P90 - P10)"} <= keys
    assert any("Forecast" in r for r in spec["data"]) and any("Actual" in r for r in spec["data"])


def test_outage_is_masked_and_gap_is_forecast_through():
    import asyncio

    from seleric_swarm.forecasting.engines import EtsEngine, impute_series
    from seleric_swarm.forecasting.quality import assess_series
    from seleric_swarm.forecasting.types import FeatureFrame

    base = [80.0 + (i % 7) for i in range(80)]
    vals = base[:60] + [10.0] * 6 + base[66:]
    idx = [date(2026, 1, 1) + timedelta(i) for i in range(80)]
    out, verdicts = assess_series("m", list(vals), idx, role="target", min_history_days=30)
    assert any(v.code == "Q_LEVEL_DIP" for v in verdicts)
    assert all(v is None for v in out[60:66])
    clean, lead = impute_series(out + [None] * 5)
    assert lead == 5 and min(clean) > 70

    frame = FeatureFrame(
        cutoff=idx[-1], context_start=idx[0],
        horizon_start=idx[-1] + timedelta(days=6), horizon_end=idx[-1] + timedelta(days=12),
        targets={"m": out + [None] * 5},
    )
    res = asyncio.run(EtsEngine().forecast(frame, target_id="m", nonnegative=True))
    assert res.days[0].date == frame.horizon_start.isoformat()
    assert 60 < res.days[0].p50 < 100


def test_driver_level_reanchors_to_driver_run_rate():
    from seleric_swarm.forecasting.driver import apply_driver_level

    idx = [date(2026, 1, 1) + timedelta(i) for i in range(60)]
    target = [100.0] * 60
    driver = {d.isoformat(): 50.0 for d in idx[:30]}
    driver.update({d.isoformat(): 100.0 for d in idx[30:]})  # driver doubled recently
    as_of = idx[-1] + timedelta(days=1)
    days = [
        DailyPoint(date=(as_of + timedelta(i)).isoformat(), mean=100, p10=80, p50=100, p90=120)
        for i in range(14)
    ]
    out = apply_driver_level(days, target=target, frame_dates=idx, driver=driver, as_of=as_of)
    assert out is not None
    adj, info = out
    assert info["level_factor"] > 1.1 and adj[0].p10 < adj[0].p50 < adj[0].p90
    assert apply_driver_level(days, target=target, frame_dates=idx, driver={}, as_of=as_of) is None


def test_select_driver_prefers_the_real_driver_and_rejects_noise():
    import random

    from seleric_swarm.forecasting.selection import select_driver

    rnd = random.Random(1)
    idx = [date(2025, 6, 1) + timedelta(i) for i in range(330)]
    spend, level = [], 100.0
    for i in range(330):
        if i % 30 == 0:
            level = rnd.uniform(50, 200)  # regime changes in the driver
        spend.append(level * rnd.uniform(0.9, 1.1))
    target = [s * 2 * rnd.uniform(0.9, 1.1) for s in spend]
    noise = [rnd.uniform(1, 100) for _ in idx]
    cands = {
        "real": {d.isoformat(): v for d, v in zip(idx, spend, strict=True)},
        "noise": {d.isoformat(): v for d, v in zip(idx, noise, strict=True)},
    }
    sel = select_driver(target, idx, cands, as_of=idx[-1] + timedelta(days=2), lag=2)
    assert sel.chosen == "real"
    assert sel.scores[0].median_error < sel.scores[-1].median_error


def test_bakeoff_keeps_only_features_that_lower_error(monkeypatch):
    import asyncio

    from seleric_swarm.forecasting import bakeoff as bk
    from seleric_swarm.forecasting.types import FeatureFrame

    n = 400
    idx = [(date(2025, 1, 1) + timedelta(i)).isoformat() for i in range(n)]
    frame = FeatureFrame(
        cutoff=date(2025, 1, 1) + timedelta(n - 3),
        context_start=date(2025, 1, 1),
        horizon_start=date(2025, 1, 1) + timedelta(n - 1),
        horizon_end=date(2025, 1, 1) + timedelta(n + 12),
        index=idx + [(date(2025, 1, 1) + timedelta(n + i)).isoformat() for i in range(14)],
        targets={"t": [100.0 + i % 7 for i in range(n)]},
        past_covariates={"good": [1.0] * n, "bad": [2.0] * n},
        future_covariates={"calendar.dow": {"past": [0.0] * n, "future": [0.0] * 14}},
    )

    async def fake(url, fr, tid, configs, origins, horizon, gap, model):
        out = {}
        for name in configs:
            out[name] = 0.10 if name == "history only" else 0.11
            if "good" in name:
                out[name] = 0.05
            if "bad" in name:
                out[name] = 0.20
        return out

    monkeypatch.setattr(bk, "_run_configs", fake)
    res = asyncio.run(
        bk.run_bakeoff(frame, "t", ["good", "bad"], chronos_url="x", known_names=["calendar.dow"], use_cache=False)
    )
    assert res.choice.past == ["good"] and res.chosen_score < res.baseline_score
    assert "good" in res.summary_line()
