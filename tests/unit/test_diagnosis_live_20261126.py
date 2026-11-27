"""D-247 P0: diagnosis faults found replaying real brand-20 events (2026-11-26), generic ids.

- a sales-like series with a week-long outage and a few negative booking days (a returns lump) was kept on the
  linear scale, so the outage looked milder than the recovery after it: the recovery was flagged abnormal and the
  outage became the "usual" level (a -51 % day reported as +74 %);
- an input metric (ad spend) was bridged through P&L lines built on its twin under another id;
- a two-day period comparison was labelled "within normal variation" at -100 %.
"""

from __future__ import annotations

import logging
import warnings
from datetime import date, timedelta

import numpy as np
import pytest

from seleric_swarm.causal.diagnosis import DiagnosisInput, MetricMeta, diagnose, seasonal_profile

pytestmark = pytest.mark.filterwarnings("ignore")
warnings.filterwarnings("ignore")
logging.getLogger("dowhy").setLevel(logging.ERROR)

N = 63
DAYS = [date(2026, 8, 1) + timedelta(days=i) for i in range(N)]
OUTAGE = range(44, 53)  # nine days
EVENT = [DAYS[-1]]


def S(values) -> dict[date, float]:
    return {d: float(v) for d, v in zip(DAYS, values, strict=True)}


def A(mid: str, deps: tuple[str, ...] = ()) -> MetricMeta:
    return MetricMeta(mid, "additive", "v1", deps, unit="XYZ")


def _sales(seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    y = 80_000 * np.exp(rng.normal(0, 0.2, N))
    y[list(OUTAGE)] = 10_000 * np.exp(rng.normal(0, 0.4, len(OUTAGE)))
    y[53:] = 70_000 * np.exp(rng.normal(0, 0.2, N - 53))
    y[[2, 31, 35, 58]] = [-180_000, -70_000, -4_000, -1_000_000]  # booking days larger than the day's sales
    return y


def test_outage_is_not_the_reference_after_recovery():
    y = _sales()
    y[-1] = 35_000  # the event: half the recovered level
    s = S(y)
    prof = seasonal_profile(s, DAYS[:-1], 1, additive=True)
    assert prof is not None and prof.use_log
    refs, _ = prof.referencer.trimmed(EVENT[0], prof.referencer.refs(EVENT[0]))
    assert refs and all(DAYS.index(r) >= 53 for r in refs)  # recovery days, never the outage
    assert not {DAYS[i] for i in range(54, 62) if y[i] > 0} & prof.outliers
    r = diagnose(DiagnosisInput(outcome="m_sales", event_days=EVENT, series={"m_sales": s},
                                lineage={"m_sales": A("m_sales")}))
    assert r.event.direction == "down" and r.event.reference > 50_000


def test_input_is_not_bridged_through_lines_built_on_its_twin():
    rng = np.random.default_rng(7)
    spend = 40_000 * np.exp(rng.normal(0, 0.15, N))
    sales = 90_000 * np.exp(rng.normal(0, 0.15, N))
    cogs = 0.4 * sales
    lineage = {
        "m_spend": A("m_spend"), "m_spend_pnl": A("m_spend_pnl"), "m_sales": A("m_sales"), "m_cogs": A("m_cogs"),
        "m_profit": A("m_profit", ("m_sales", "m_cogs", "m_spend_pnl")),  # built on the twin, not on m_spend
    }
    series = {"m_spend": S(spend), "m_spend_pnl": S(spend), "m_sales": S(sales), "m_cogs": S(cogs),
              "m_profit": S(sales - cogs - spend)}
    series["m_spend"][DAYS[-1]] = series["m_spend_pnl"][DAYS[-1]] = 0.0
    r = diagnose(DiagnosisInput(outcome="m_spend", event_days=EVENT, series=series, lineage=lineage,
                                bridge_candidates=["m_spend_pnl", "m_sales", "m_cogs", "m_profit"]))
    assert not any("m_profit" in line for line in r.bridge_identity)


def test_short_period_comparison_is_sized_against_daily_noise():
    rng = np.random.default_rng(11)
    spend = 40_000 * np.exp(rng.normal(0, 0.1, N))
    spend[-2:] = 0.0
    r = diagnose(DiagnosisInput(outcome="m_spend", event_days=DAYS[-2:], series={"m_spend": S(spend)},
                                lineage={"m_spend": A("m_spend")}, baseline_days=DAYS[-4:-2]))
    assert r.event.strength == "strong"
