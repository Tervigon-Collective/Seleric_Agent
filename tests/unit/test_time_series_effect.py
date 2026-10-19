"""estimate_time_series_effect (explicit-DAG DoWhy estimator) on known-truth data.

The legacy estimator labelled 4 of 5 null scenarios below CAUSALLY_SUPPORTED;
each must now either recover the true effect or report an interval containing 0.
"""

from __future__ import annotations

import logging
import warnings
from datetime import date, timedelta

import numpy as np
import pytest

from seleric_swarm.causal.dowhy_service import estimate_time_series_effect

pytestmark = pytest.mark.filterwarnings("ignore")
warnings.filterwarnings("ignore")
logging.getLogger("dowhy").setLevel(logging.ERROR)

N = 56
T = np.arange(N)
DAYS = [date(2026, 8, 1) + timedelta(days=int(i)) for i in T]


def S(a):
    return {d: float(v) for d, v in zip(DAYS, a, strict=True)}


def run(x, y, others=None):
    return estimate_time_series_effect(
        outcome=S(y), treatment=S(x), others={k: S(v) for k, v in (others or {}).items()},
        days=DAYS, treatment_name="x", outcome_name="y",
    )


def covers(r, value):
    lo, hi = r.confidence_interval
    return lo <= value <= hi


def test_weekly_seasonality_confounding_is_adjusted_away():
    rng = np.random.default_rng(0)
    wk = (T % 7 >= 5).astype(float) * 50
    r = run(100 + wk + rng.normal(0, 3, N), 500 + 4 * wk + rng.normal(0, 10, N))
    assert r.naive_association["p_value"] < 0.05
    assert covers(r, 0.0)


def test_mediator_is_not_adjusted_for():
    rng = np.random.default_rng(1)
    x = rng.normal(100, 10, N)
    m = 2 * x + rng.normal(0, 2, N)
    r = run(x, 3 * m + rng.normal(0, 5, N), {"m": m})
    assert "m" not in r.adjustment_set and covers(r, 6.0)


def test_collider_only_enters_lagged():
    rng = np.random.default_rng(2)
    x, y = rng.normal(100, 10, N), rng.normal(500, 20, N)
    r = run(x, y, {"c": x + y + rng.normal(0, 2, N)})
    assert "c" not in r.adjustment_set and covers(r, 0.0)


def test_reverse_causality_is_flagged_as_feedback():
    rng = np.random.default_rng(3)
    y = 500 + np.cumsum(rng.normal(0, 10, N))
    x = np.r_[100, 0.2 * y[:-1]] + rng.normal(0, 1, N)
    r = run(x, y)
    assert r.temporal["direction"] == "feedback"


def test_shared_trend_is_not_an_effect():
    rng = np.random.default_rng(4)
    r = run(100 + 3 * T + rng.normal(0, 3, N), 500 + 10 * T + rng.normal(0, 10, N))
    assert covers(r, 0.0)


def test_true_effect_recovered_with_passing_placebos():
    rng = np.random.default_rng(5)
    x = 100 + 20 * np.sin(T / 3) + rng.normal(0, 5, N)
    r = run(x, 300 + 2 * x + rng.normal(0, 10, N))
    assert covers(r, 2.0) and not covers(r, 0.0)
    passed = {c["name"]: c["passed"] for c in r.refutations}
    assert passed["time_shift_placebo"] and passed["future_treatment_placebo"]


def test_lagged_common_cause_is_controlled():
    rng = np.random.default_rng(6)
    z = rng.normal(0, 10, N)
    x = 100 + z + rng.normal(0, 3, N)
    y = 500 + 5 * np.r_[0, z[:-1]] + rng.normal(0, 5, N)
    r = run(x, y, {"z": z})
    assert covers(r, 0.0)


def test_constant_treatment_is_insufficient():
    rng = np.random.default_rng(7)
    r = run(np.full(N, 5.0), rng.normal(100, 5, N))
    assert r.status == "insufficient" and "barely varies" in r.reason
