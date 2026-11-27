"""Probability judgments for low-volume ad entities. Pure functions.

Purchases are a Poisson process in spend with rate lambda (purchases per rupee). The prior is Gamma with mean equal to
the parent's historical rate and the weight of ``prior_events`` purchases, so a new ad with 1 purchase on 300 rupees
is pulled toward its ad set instead of reading as a 300-rupee CPA. CPA = 1 / lambda and ROAS ~ lambda * AOV.

Click-through and conversion are Binomial with a Beta prior centred on the baseline, weighted as ``prior_trials``.
"""

from __future__ import annotations

import math

from scipy import stats


def gamma_posterior(events: float, exposure: float, prior_rate: float, prior_events: float) -> tuple[float, float]:
    """(shape, rate) of the posterior on lambda. ``prior_rate`` > 0, ``prior_events`` > 0."""
    a0 = max(prior_events, 1e-6)
    b0 = a0 / max(prior_rate, 1e-12)
    return a0 + max(events, 0.0), b0 + max(exposure, 0.0)


def prob_rate_below(threshold: float, events: float, exposure: float, prior_rate: float, prior_events: float) -> float:
    a, b = gamma_posterior(events, exposure, prior_rate, prior_events)
    return float(stats.gamma.cdf(threshold, a, scale=1.0 / b))


def prob_cpa_above(allowable_cpa: float, purchases: float, spend: float, prior_rate: float, prior_events: float) -> float:
    """P(true CPA > allowable) = P(lambda < 1 / allowable)."""
    return prob_rate_below(1.0 / allowable_cpa, purchases, spend, prior_rate, prior_events)


def shrunk_aov(value: float, purchases: float, prior_aov: float, prior_orders: float) -> float:
    return (max(value, 0.0) + prior_aov * prior_orders) / (max(purchases, 0.0) + prior_orders)


def prob_roas_below(line: float, purchases: float, spend: float, value: float, prior_rate: float, prior_aov: float,
                    prior_events: float, prior_orders: float = 3.0) -> float:
    """P(true ROAS < line), with AOV shrunk to its prior and treated as known."""
    aov = shrunk_aov(value, purchases, prior_aov, prior_orders)
    return prob_rate_below(line / aov, purchases, spend, prior_rate, prior_events)


def prob_zero(rate: float, exposure: float) -> float:
    """P(no purchase on ``exposure`` rupees) when the true rate is ``rate``."""
    return math.exp(-max(rate, 0.0) * max(exposure, 0.0))


def beta_posterior(successes: float, trials: float, prior_p: float, prior_trials: float) -> tuple[float, float]:
    p = min(max(prior_p, 1e-6), 1 - 1e-6)
    a0, b0 = p * prior_trials, (1 - p) * prior_trials
    s = min(max(successes, 0.0), max(trials, 0.0))
    return a0 + s, b0 + max(trials, 0.0) - s


def prob_p_below(threshold: float, successes: float, trials: float, prior_p: float, prior_trials: float) -> float:
    a, b = beta_posterior(successes, trials, prior_p, prior_trials)
    return float(stats.beta.cdf(min(max(threshold, 0.0), 1.0), a, b))


def quantiles(values: list[float]) -> tuple[float, float, float] | None:
    xs = sorted(v for v in values if v is not None and math.isfinite(v))
    if not xs:
        return None

    def q(p: float) -> float:
        k = (len(xs) - 1) * p
        lo = math.floor(k)
        hi = min(lo + 1, len(xs) - 1)
        return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)

    return q(0.1), q(0.5), q(0.9)
