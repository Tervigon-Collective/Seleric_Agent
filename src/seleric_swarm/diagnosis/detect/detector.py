"""The shared anomaly primitives (plan P1.4, D-8).

Four places in the service score "how unusual is this number", and they do not use one formula on purpose:

* the diagnosis engine centres and scales model errors with median/MAD (``robust_center_scale``);
* business state scores a reading against a history band (``robust_score``, ``rescore``);
* forecasting data quality flags outliers with a modified z-score (``modified_z``);
* exploration tests one new window against a few earlier ones with a prediction interval (``prediction_t``), because
  a median/MAD scale "ran anti-conservative" at the five or six windows it has.

Each keeps its own rule and floor, now written once, named, and tested for exact equality against the original
formulas (``tests/unit/diagnosis/test_detector_parity.py``). The callers delegate here, so there is one place to read
and one place to change them, and no output moves."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

MAD_TO_SIGMA = 1.4826  # median absolute deviation -> standard deviation for normal data
MODIFIED_Z_FACTOR = 0.6745  # inverse of the above, the modified z-score constant
FLAT_SIGMA = 1e-9  # scale of a series that does not move at all
_EPS = 1e-12


def is_finite(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def median(xs: Sequence[float]) -> float:
    """The middle value (mean of the two middle ones for an even count); NaN for no values."""
    s = sorted(xs)
    n = len(s)
    if not n:
        return math.nan
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else math.nan


def robust_center_scale(xs: Sequence[float]) -> tuple[float, float]:
    """Median and a robust sigma (diagnosis rule): MAD x 1.4826; if the MAD is zero, the population standard
    deviation; if that is zero too, ``FLAT_SIGMA``."""
    centre = median(xs)
    sigma = median([abs(x - centre) for x in xs]) * MAD_TO_SIGMA
    if sigma <= 0:
        sd = math.sqrt(mean([(x - centre) ** 2 for x in xs])) if xs else 0.0
        sigma = sd if sd > 0 else FLAT_SIGMA
    return centre, sigma


@dataclass
class RobustScore:
    observed: float
    expected: float
    expected_range: list[float]
    deviation_pct: float | None
    score: float
    direction: str
    is_anomaly: bool


def _score(observed: float, expected: float, sigma: float, z_threshold: float) -> RobustScore:
    z = (observed - expected) / sigma
    return RobustScore(
        observed=observed,
        expected=expected,
        expected_range=[expected - z_threshold * sigma, expected + z_threshold * sigma],
        deviation_pct=((observed - expected) / abs(expected) * 100) if expected else None,
        score=abs(z),
        direction="up" if observed > expected else ("down" if observed < expected else "flat"),
        is_anomaly=abs(z) >= z_threshold,
    )


def robust_score(history: Sequence[float], observed: float, *, z_threshold: float = 3.0) -> RobustScore:
    """Business-state rule: score ``observed`` against the median/MAD band of ``history`` (which excludes it). A flat
    history (MAD zero) uses ``FLAT_SIGMA``, so any move on a flat metric scores as large, never as 'not anomalous'."""
    if not history:
        raise ValueError("robust_score needs at least one history point")
    centre = statistics.median(history)
    mad = statistics.median([abs(v - centre) for v in history])
    sigma = MAD_TO_SIGMA * mad if mad else FLAT_SIGMA
    return _score(observed, centre, sigma, z_threshold)


def rescore(observed: float, *, expected: float, expected_range: Sequence[float], z_threshold: float) -> RobustScore:
    """Score another ``observed`` against a band computed earlier. The band is symmetric about ``expected``, so its
    sigma is recoverable from either edge without the raw history."""
    sigma = (expected_range[1] - expected) / z_threshold if z_threshold else 0.0
    if sigma <= 0:
        sigma = FLAT_SIGMA
    z = (observed - expected) / sigma
    return RobustScore(
        observed=observed,
        expected=expected,
        expected_range=list(expected_range),
        deviation_pct=((observed - expected) / abs(expected) * 100) if expected else None,
        score=abs(z),
        direction="up" if observed > expected else ("down" if observed < expected else "flat"),
        is_anomaly=abs(z) >= z_threshold,
    )


def modified_z(value: float, centre: float, raw_mad: float) -> float:
    """Forecasting rule: 0.6745 x (value - median) / MAD, with a MAD of zero taken as 1.0."""
    return MODIFIED_Z_FACTOR * (value - centre) / (raw_mad or 1.0)


def prediction_t(observed: float, history: Sequence[float]) -> tuple[float, int] | None:
    """(t, df) of a new observation against a small sample of earlier ones (exploration rule).

    The exact normal prediction interval ``t = (x - mean) / (s * sqrt(1 + 1/n))`` with ``n - 1`` degrees of freedom.
    With the five or six windows an exploration has, a median/MAD scale is too unstable (it ran anti-conservative
    under pure noise); an outlier in the history only widens ``s``, which errs toward reporting less."""
    xs = [x for x in history if math.isfinite(x)]
    if len(xs) < 3 or not math.isfinite(observed):
        return None
    n = len(xs)
    centre = float(np.mean(xs))
    sd = float(np.std(xs, ddof=1))
    if sd <= _EPS:
        sd = max(abs(centre) * 0.01, _EPS)
    return (observed - centre) / (sd * math.sqrt(1 + 1 / n)), n - 1
