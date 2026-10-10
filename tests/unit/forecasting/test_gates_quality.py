"""Gate reason codes, quality verdicts, and leakage canary."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from seleric_swarm.forecasting.gates import (
    apply_temporal_cutoff,
    check_certified,
    check_future_role,
    check_identity,
    cutoff_for,
    leakage_canary_mask,
)
from seleric_swarm.forecasting.policies import FeatureBundle, ForecastPolicies, load_forecast_policies
from seleric_swarm.forecasting.quality import assess_series
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot


def _index(n: int, start: date = date(2026, 1, 1)) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def test_cutoff_excludes_today():
    as_of = date(2026, 10, 9)
    c = cutoff_for(as_of=as_of, maturity_days=0)
    assert c == date(2026, 10, 8)


def test_cutoff_applies_maturity():
    as_of = date(2026, 10, 9)
    c = cutoff_for(as_of=as_of, maturity_days=21)
    assert c == date(2026, 9, 17)  # as_of-1 - 21


def test_temporal_cutoff_masks_after():
    idx = _index(10, date(2026, 10, 1))
    values = [float(i) for i in range(10)]
    masked, decisions = apply_temporal_cutoff(
        values, idx, cutoff=date(2026, 10, 5), series="net_sales"
    )
    assert masked[5:] == [None] * 5
    assert any(d.code == "T_CUTOFF" for d in decisions)


def test_future_role_blocks_metric():
    d = check_future_role("ad_spend", "known_future")
    assert d is not None and d.code == "T_FUTURE_ROLE"
    assert check_future_role("calendar.dow", "known_future") is None


def test_identity_blocks_composition_as_covariate():
    d = check_identity(
        "net_sales", "orders", composition_ids={"orders", "cogs"}, role="past_covariate"
    )
    assert d is not None and d.code == "T_IDENTITY"
    assert (
        check_identity("net_sales", "orders", composition_ids={"orders"}, role="co_target")
        is None
    )


def test_leakage_canary_masks_lookahead():
    """A covariate equal to the target shifted −k must be masked at the cutoff."""
    n = 30
    idx = _index(n, date(2026, 9, 1))
    target = [float(i) for i in range(n)]
    lag = 3
    # covariate[t] = target[t+lag] (look-ahead)
    covariate: list[float | None] = [None] * n
    for i in range(n - lag):
        covariate[i] = target[i + lag]
    cutoff = date(2026, 9, 20)
    masked, decisions = leakage_canary_mask(
        target, covariate, idx, cutoff=cutoff, lag_days=lag
    )
    assert any(d.code in {"T_CUTOFF", "T_FEATURE_MATURITY"} for d in decisions)
    for i, d in enumerate(idx):
        if d + timedelta(days=lag) > cutoff:
            assert masked[i] is None


def test_known_future_metric_fails_schema_load():
    with pytest.raises(ValidationError):
        FeatureBundle(
            id="bad",
            known_future=["ad_spend"],  # must be calendar.*
        )


def test_policy_file_loads():
    policies = load_forecast_policies()
    assert "net_sales" in policies.targets
    assert "orders" in policies.targets
    status, _ = policies.eligibility("net_sales")
    assert status == "validated"


def test_outage_zero_run_masked():
    idx = _index(40)
    values: list[float | None] = [10.0] * 40
    values[20] = 0.0
    values[21] = 0.0
    values[22] = 0.0
    out, verdicts = assess_series(
        "net_sales", values, idx, role="target", min_history_days=10, nonnegative=True
    )
    assert any(v.code == "Q_OUTAGE_ZERO_RUN" for v in verdicts)
    assert out[20] is None and out[21] is None and out[22] is None


def test_negative_blocks():
    idx = _index(15)
    values = [1.0] * 15
    values[7] = -3.0
    _, verdicts = assess_series(
        "orders", values, idx, role="target", min_history_days=10, nonnegative=True
    )
    assert any(v.code == "Q_NEGATIVE" and v.action == "block" for v in verdicts)


def test_short_history_blocks_target():
    idx = _index(5)
    _, verdicts = assess_series(
        "orders", [1.0] * 5, idx, role="target", min_history_days=120
    )
    assert any(v.code == "Q_SHORT_HISTORY" and v.action == "block" for v in verdicts)


def test_coverage_ignores_leading_empty_retention_gap():
    """A long requested window with data only in the recent half must pass
    coverage when that half is dense (the live net_sales refusal)."""
    idx = _index(200)
    values: list[float | None] = [None] * 100 + [10.0] * 100
    _, verdicts = assess_series(
        "net_sales", values, idx, role="target", min_history_days=80
    )
    assert not any(v.code == "Q_COVERAGE" and v.action == "block" for v in verdicts)


def test_coverage_warns_not_blocks_near_90_full_span():
    """Live case: ~89.8% over two years with a dense recent tail must proceed."""
    idx = _index(695)
    values: list[float | None] = [10.0] * 695
    # 71 gaps only in the early portion → 624/695 ≈ 89.8%; last 90 intact
    early = 695 - 90
    for i in range(71):
        values[i * (early // 71)] = None
    for i in range(early, 695):
        values[i] = 10.0
    present = sum(v is not None for v in values)
    assert present / 695 < 0.90  # still under the old flat rule
    _, verdicts = assess_series(
        "net_sales", values, idx, role="target", min_history_days=120
    )
    assert not any(v.code == "Q_COVERAGE" and v.action == "block" for v in verdicts)
    assert any(v.code == "Q_COVERAGE" and v.action == "warn" for v in verdicts)


def test_coverage_blocks_sparse_recent_window():
    idx = _index(100)
    values: list[float | None] = [10.0] * 100
    # Punch holes in the last 90 days
    for i in range(20, 100, 2):
        values[i] = None
    _, verdicts = assess_series(
        "net_sales", values, idx, role="target", min_history_days=10
    )
    assert any(v.code == "Q_COVERAGE" and v.action == "block" for v in verdicts)


def test_certified_unknown_blocks():
    snap = CatalogueSnapshot(metrics=())
    d = check_certified("net_sales", catalogue=snap)
    assert d is not None and d.code == "T_CERTIFIED"


def test_certified_ok_when_present():
    snap = CatalogueSnapshot(
        metrics=(CatalogueMetricMeta(id="net_sales", raw={"status": "certified"}),)
    )
    assert check_certified("net_sales", catalogue=snap) is None
