"""Shared detector parity (plan P1.4, Gate 1): every primitive returns exactly what the original formula returned.

The ``legacy_*`` functions below are the original code, copied verbatim from before the move; the shared functions must
agree with them bit for bit on randomized and awkward inputs (flat, tied, negative, zero-heavy, even and odd counts)."""

from __future__ import annotations

import math
import statistics

import numpy as np
import pytest

from seleric_swarm.causal import diagnosis as engine
from seleric_swarm.diagnosis.detect import detector as D
from seleric_swarm.diagnosis.detect import reference as R
from seleric_swarm.exploration import insights
from seleric_swarm.services.business_state import detectors as business


# ---------------------------------------------------------------- the original code, verbatim
def _median(xs):
    s = sorted(xs)
    n = len(s)
    if not n:
        return math.nan
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _mean(xs):
    return sum(xs) / len(xs) if xs else math.nan


def legacy_robust_center_scale(xs):  # causal/diagnosis.py
    c = _median(xs)
    mad = _median([abs(x - c) for x in xs]) * 1.4826
    if mad <= 0:
        sd = math.sqrt(_mean([(x - c) ** 2 for x in xs])) if xs else 0.0
        mad = sd if sd > 0 else 1e-9
    return c, mad


def legacy_robust_zscore(history, observed, z_threshold=3.0):  # services/business_state/detectors.py
    median = statistics.median(history)
    mad = statistics.median([abs(v - median) for v in history])
    sigma = 1.4826 * mad if mad else 1e-9
    z = (observed - median) / sigma
    deviation_pct = ((observed - median) / abs(median) * 100) if median else None
    direction = "up" if observed > median else ("down" if observed < median else "flat")
    return {
        "observed": observed, "expected": median, "expected_range": [median - z_threshold * sigma, median + z_threshold * sigma],
        "deviation_pct": deviation_pct, "score": abs(z), "direction": direction, "is_anomaly": abs(z) >= z_threshold,
    }


def legacy_rescore(observed, *, expected, expected_range, z_threshold):  # services/business_state/detectors.py
    sigma = (expected_range[1] - expected) / z_threshold if z_threshold else 0.0
    if sigma <= 0:
        sigma = 1e-9
    z = (observed - expected) / sigma
    deviation_pct = ((observed - expected) / abs(expected) * 100) if expected else None
    direction = "up" if observed > expected else ("down" if observed < expected else "flat")
    return {
        "observed": observed, "expected": expected, "expected_range": expected_range, "deviation_pct": deviation_pct,
        "score": abs(z), "direction": direction, "is_anomaly": abs(z) >= z_threshold,
    }


def legacy_outlier_z(v, med, observed):  # forecasting/quality.py Q_OUTLIER
    mad = statistics.median([abs(x - med) for x in observed]) or 1.0
    return 0.6745 * (v - med) / mad


def legacy_prediction_t(observed, history):  # exploration/insights.py
    _EPS = 1e-12
    xs = [x for x in history if math.isfinite(x)]
    if len(xs) < 3 or not math.isfinite(observed):
        return None
    n = len(xs)
    mean = float(np.mean(xs))
    sd = float(np.std(xs, ddof=1))
    if sd <= _EPS:
        sd = max(abs(mean) * 0.01, _EPS)
    return (observed - mean) / (sd * math.sqrt(1 + 1 / n)), n - 1


# ---------------------------------------------------------------- inputs
def _samples(seed: int, count: int = 400):
    rng = np.random.default_rng(seed)
    for i in range(count):
        n = int(rng.integers(1, 40))
        kind = i % 8
        if kind == 0:
            xs = rng.normal(100, 15, n)
        elif kind == 1:
            xs = np.full(n, float(rng.integers(-5, 50)))  # flat
        elif kind == 2:
            xs = rng.integers(0, 4, n).astype(float)  # heavy ties, zeros
        elif kind == 3:
            xs = -np.abs(rng.normal(80, 20, n))  # negative base
        elif kind == 4:
            xs = np.concatenate([rng.normal(10, 1, n), [1e6]])  # one huge outlier
        elif kind == 5:
            xs = np.zeros(n)
        elif kind == 6:
            xs = rng.lognormal(3, 1.2, n)
        else:
            xs = np.concatenate([np.full(n, 7.0), [7.0000001]])
        yield [float(x) for x in xs], float(rng.normal(float(np.mean(xs)), 25))


def test_robust_center_scale_equals_the_original_exactly():
    checked = 0
    for xs, _ in _samples(1):
        assert D.robust_center_scale(xs) == legacy_robust_center_scale(xs)
        assert engine._robust_center_scale(xs) == legacy_robust_center_scale(xs)  # the engine now delegates
        checked += 1
    assert checked == 400
    assert math.isnan(D.median([])) and all(math.isnan(v) for v in D.robust_center_scale([]))  # as the original
    assert all(math.isnan(v) for v in legacy_robust_center_scale([]))


@pytest.mark.parametrize("z", [3.0, 2.0, 1.5])
def test_robust_score_and_rescore_equal_the_originals_exactly(z):
    for xs, observed in _samples(2):
        got = D.robust_score(xs, observed, z_threshold=z)
        assert vars(got) == legacy_robust_zscore(xs, observed, z)
        assert vars(business.robust_zscore(xs, observed, z_threshold=z)) == legacy_robust_zscore(xs, observed, z)
        band = legacy_robust_zscore(xs, xs[-1], z)
        args = {"expected": band["expected"], "expected_range": band["expected_range"], "z_threshold": z}
        assert vars(D.rescore(observed, **args)) == legacy_rescore(observed, **args)
        assert vars(business._rescore_against_expected(observed, **args)) == legacy_rescore(observed, **args)


def test_business_state_keeps_its_names_and_its_empty_history_error():
    assert business.RobustZScoreResult is D.RobustScore
    with pytest.raises(ValueError):
        business.robust_zscore([], 1.0)
    assert D.rescore(5.0, expected=1.0, expected_range=[1.0, 1.0], z_threshold=3.0).score == 4.0 / 1e-9  # flat band guard


def test_modified_z_equals_the_original_outlier_rule_exactly():
    for xs, _ in _samples(3):
        med = statistics.median(xs)
        mad = statistics.median([abs(x - med) for x in xs])
        for v in (xs[0], xs[-1], med + 100.0, med - 7.5):
            assert D.modified_z(v, med, mad) == legacy_outlier_z(v, med, xs)


def test_prediction_t_equals_the_original_exactly_and_exploration_uses_it():
    assert insights.prediction_t is D.prediction_t
    for xs, observed in _samples(4):
        assert D.prediction_t(observed, xs) == legacy_prediction_t(observed, xs)
    assert D.prediction_t(1.0, [1.0, float("nan"), 2.0]) is None  # fewer than three finite values
    assert D.prediction_t(float("inf"), [1.0, 2.0, 3.0]) is None


def test_the_engine_uses_the_moved_reference_code_not_a_copy():
    assert engine.Referencer is R.Referencer
    assert engine._prior_days is R.prior_days
    assert (R.LEVEL_DAYS, R.LEVEL_FACTOR_BOUNDS) == (7, (0.2, 5.0))


def test_the_reference_picks_the_same_weekday_days_before_a_day():
    from datetime import date, timedelta

    pool = {date(2026, 9, 1) + timedelta(days=i) for i in range(60)}
    day = date(2026, 10, 20)
    same = R.prior_days(day, pool, 4, set(), True)
    assert same == [day - timedelta(days=7 * j) for j in (1, 2, 3, 4)] and all(d.weekday() == day.weekday() for d in same)
    skipped = R.prior_days(day, pool, 4, {day - timedelta(days=7)}, True)
    assert skipped[0] == day - timedelta(days=14)
    recent = R.prior_days(day, pool, 4, set(), False)
    assert len(recent) == 7 and recent[0] == day - timedelta(days=1)
