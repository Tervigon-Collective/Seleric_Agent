"""Diagnosis fixes from replaying real 2026-10-04..07 incidents (brand 20).

Each scenario reproduces the SHAPE of a live failure with generic ids:
- a click-through-like rate halved when a new near-zero-rate segment took half
  the volume; the engine called it "normal" (forced same-weekday reference on a
  series with no weekly pattern), weighted segments by the numerator, and
  pinned the mix effect on the segments whose rate held;
- a return-on-spend-like ratio whose numerator has volume in segments with no
  denominator (unattributed sales) could not be localised at all;
- dimensions carried by every view (conformed business axes) were treated as
  the tenant key and never examined.
"""

from __future__ import annotations

import logging
import warnings
from datetime import date, timedelta

import numpy as np
import pytest

from seleric_swarm.causal import diagnosis as engine
from seleric_swarm.causal.diagnosis import DiagnosisInput, MetricMeta, diagnose
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
from seleric_swarm.toolsets import diagnosis as tool

pytestmark = pytest.mark.filterwarnings("ignore")
warnings.filterwarnings("ignore")
logging.getLogger("dowhy").setLevel(logging.ERROR)

N = 57
DAYS = [date(2026, 8, 8) + timedelta(days=i) for i in range(N)]
EVENT = DAYS[-2:]


def S(values) -> dict[date, float]:
    return {d: float(v) for d, v in zip(DAYS, values, strict=True)}


def A(mid: str, view: str = "v1", deps: tuple[str, ...] = (), unit: str = "") -> MetricMeta:
    return MetricMeta(mid, "additive", view, deps, unit=unit)


def R(mid: str, view: str = "v1", deps: tuple[str, ...] = (), unit: str = "ratio") -> MetricMeta:
    return MetricMeta(mid, "ratio", view, deps, unit=unit)


def _new_segment_mix_shift(seed: int = 5):
    """m_rate = m_num / m_den; segment "new" appears on the event days with ~0 rate."""
    rng = np.random.default_rng(seed)
    level = np.where(np.arange(N) < 20, 0.035, 0.02)  # a regime shift, no weekday pattern
    den_old = 100_000 * np.exp(rng.normal(0, 0.15, N))
    rate_old = level * np.exp(rng.normal(0, 0.05, N))
    den_new = np.zeros(N)
    den_new[-2:] = [160_000, 120_000]
    rate_new = np.where(den_new > 0, 0.0004, 0.0)
    num_old, num_new = den_old * rate_old, den_new * rate_new
    den = den_old + den_new
    num = num_old + num_new
    lineage = {
        "m_rate": R("m_rate", "v1", ("m_num", "m_den")),
        "m_num": A("m_num", "v1"),
        "m_den": A("m_den", "v1"),
    }
    series = {"m_rate": S(num / den), "m_num": S(num), "m_den": S(den)}
    rates = {"old": S(rate_old), "new": S(rate_new)}
    weights = {"old": S(den_old), "new": {d: v for d, v in S(den_new).items() if v > 0}}
    return lineage, series, rates, weights


def test_rate_without_weekly_pattern_is_referenced_on_nearest_days_and_flagged():
    lineage, series, rates, weights = _new_segment_mix_shift()
    r = diagnose(DiagnosisInput(
        outcome="m_rate", event_days=EVENT, series=series, lineage=lineage,
        segments={"m_rate": {"dim_a": rates}, "m_den": {"dim_a": weights}},
        denominators={"m_rate": "m_den"}, claimed_direction="down",
    ))
    assert r.event.reference_kind.startswith("nearest")
    assert r.event.unusual and r.event.premise == "confirmed"
    dim = r.dimensions[0]
    assert dim.kind == "rate" and dim.localised
    assert abs(dim.mix_effect) > abs(dim.rate_effect)  # the mix, not the rates, carried it
    # The mix effect sits on the segment that GAINED volume at a low rate.
    top = dim.top[0]
    assert top.segment == "new" and top.volume_share_reference == pytest.approx(0.0, abs=1e-9)
    assert abs(top.mix_effect) > abs(next(s for s in dim.top if s.segment == "old").mix_effect)
    where = next(line for line in r.narrative if line.startswith("WHERE"))
    assert "new in this period" in where


def test_weekly_pattern_test_tells_seasonal_from_flat():
    rng = np.random.default_rng(2)
    dow = np.array([d.weekday() for d in DAYS])
    weekly = S(1000 - 400 * (dow == 6) + rng.normal(0, 20, N))
    flat = S(np.where(np.arange(N) < 25, 0.03, 0.02) * np.exp(rng.normal(0, 0.05, N)))
    assert engine._has_weekly_pattern(weekly, set(DAYS))
    assert not engine._has_weekly_pattern(flat, set(DAYS))


def test_ratio_of_sums_localises_when_numerator_has_unattributed_volume():
    rng = np.random.default_rng(9)
    den_a = 5000 * np.exp(rng.normal(0, 0.08, N))
    den_b = 5000 * np.exp(rng.normal(0, 0.08, N))
    num_a = den_a * 1.5 * np.exp(rng.normal(0, 0.08, N))
    num_b = den_b * 1.5 * np.exp(rng.normal(0, 0.08, N))
    num_u = 3000 * np.exp(rng.normal(0, 0.08, N))  # numerator with no denominator anywhere
    den_a[-2:] *= 3.0  # segment a's denominator tripled, its numerator did not follow
    num, den = num_a + num_b + num_u, den_a + den_b
    lineage = {
        "m_ret": R("m_ret", "v1", ("m_num", "m_den")),
        "m_num": A("m_num", "v2", unit="XYZ"),
        "m_den": A("m_den", "v3", unit="XYZ"),
    }
    seg_ret = {"a": S(num_a / den_a), "b": S(num_b / den_b)}
    r = diagnose(DiagnosisInput(
        outcome="m_ret", event_days=EVENT, lineage=lineage,
        series={"m_ret": S(num / den), "m_num": S(num), "m_den": S(den)},
        segments={
            "m_ret": {"dim_a": seg_ret},
            "m_den": {"dim_a": {"a": S(den_a), "b": S(den_b)}},
            "m_num": {"dim_a": {"a": S(num_a), "b": S(num_b), "(not set)": S(num_u)}},
        },
        denominators={"m_ret": "m_den"}, numerators={"m_ret": "m_num"},
    ))
    dim = next(d for d in r.dimensions if d.dimension == "dim_a")
    assert dim.kind == "ratio" and dim.numerator == "m_num" and dim.denominator == "m_den"
    assert dim.coverage == pytest.approx(1.0, abs=1e-6)
    assert dim.top[0].segment == "a" and dim.localised
    # A multiple, not a share: 1.5 must never print as 150%.
    happened = next(line for line in r.narrative if line.startswith("WHAT HAPPENED"))
    assert "x on " in happened and "% on " not in happened


def test_rate_parts_reads_the_denominator_from_the_data_across_views():
    rng = np.random.default_rng(4)
    num = 2000 * np.exp(rng.normal(0, 0.1, N))
    den = 1500 * np.exp(rng.normal(0, 0.1, N))
    lineage = {
        "m_ret": R("m_ret", "v1", ("m_num", "m_den")),
        "m_num": A("m_num", "v2"),
        "m_den": A("m_den", "v3"),
        "m_per": R("m_per", "v3", ("m_den",)),
    }
    series = {"m_ret": S(num / den), "m_num": S(num), "m_den": S(den)}
    assert tool._rate_parts("m_ret", lineage, series, DAYS) == ("m_num", "m_den")
    # Listed the other way round, the data still name the divisor.
    flipped = {**lineage, "m_ret": R("m_ret", "v1", ("m_den", "m_num"))}
    assert tool._rate_parts("m_ret", flipped, series, DAYS) == ("m_num", "m_den")
    assert tool._rate_parts("m_per", lineage, series, DAYS) == (None, "m_den")


class _Ctx:
    def __init__(self, catalogue: CatalogueSnapshot) -> None:
        self.deps = type("D", (), {"catalogue": catalogue})()


def test_a_dimension_on_every_view_is_still_a_split_unless_it_is_the_tenant_key():
    metrics = tuple(
        CatalogueMetricMeta(id=f"m_{v}", view=v, supported_dimensions=["brand_id", "dim_everywhere", f"dim_{v}"])
        for v in ("v1", "v2", "v3")
    )
    ctx = _Ctx(CatalogueSnapshot(metrics=metrics, dimensions=("brand_id", "dim_everywhere")))
    assert tool._scope_dimensions(ctx) == {"brand_id"}  # type: ignore[arg-type]
    assert "dim_everywhere" in tool._plan_dimensions(ctx, "m_v1", 8)  # type: ignore[arg-type]


def test_unattributed_rows_are_kept_as_one_segment():
    rows = [
        {"x.day.day": "2026-10-01T00:00:00.000", "x.dim_a": "a", "m": 2.0},
        {"x.day.day": "2026-10-01T00:00:00.000", "x.dim_a": None, "m": 3.0},
        {"x.day.day": "2026-10-01T00:00:00.000", "x.dim_a": "", "m": 1.0},
    ]
    segs = tool._parse_segments({"rows": rows}, "m", "dim_a")
    assert segs["a"][date(2026, 10, 1)] == 2.0
    assert segs[tool._UNSET_SEGMENT][date(2026, 10, 1)] == 4.0


def test_period_comparison_splits_a_long_window_against_the_previous_one():
    """MS3-809247dd65: "why did ROAS change versus the previous period?" over 30
    days was refused (14-day cap) and the answer restated two totals."""
    rng = np.random.default_rng(12)
    n = 30
    days = [date(2026, 7, 1) + timedelta(days=i) for i in range(4 * n)]
    event, base = days[-n:], days[-2 * n : -n]
    den_a = 4000 * np.exp(rng.normal(0, 0.1, len(days)))
    den_b = 2000 * np.exp(rng.normal(0, 0.1, len(days)))
    num_a = den_a * 0.6 * np.exp(rng.normal(0, 0.1, len(days)))
    num_b = den_b * 0.6 * np.exp(rng.normal(0, 0.1, len(days)))
    num_u = 800 * np.exp(rng.normal(0, 0.1, len(days)))
    den_a[-n:] *= 0.4  # segment a's spend was cut; its return per unit cut was low
    num_a[-n:] *= 0.8
    den_a[-5:] = 0.0  # an outage: no denominator at all on some days
    num, den = num_a + num_b + num_u, den_a + den_b
    s = lambda a: {d: float(v) for d, v in zip(days, a, strict=True)}
    ratio = {d: float(x / y) for d, x, y in zip(days, num, den, strict=True) if y > 0}
    seg_ratio = lambda nn, dd: {d: float(x / y) for d, x, y in zip(days, nn, dd, strict=True) if y > 0}
    lineage = {"m_ret": R("m_ret", "v1", ("m_num", "m_den")), "m_num": A("m_num", "v2"), "m_den": A("m_den", "v3")}
    r = diagnose(DiagnosisInput(
        outcome="m_ret", event_days=event, baseline_days=base, lineage=lineage,
        series={"m_ret": ratio, "m_num": s(num), "m_den": {d: v for d, v in s(den).items() if v > 0}},
        segments={
            "m_ret": {"dim_a": {"a": seg_ratio(num_a, den_a), "b": seg_ratio(num_b, den_b)}},
            "m_den": {"dim_a": {"a": {d: v for d, v in s(den_a).items() if v > 0}, "b": s(den_b)}},
            "m_num": {"dim_a": {"a": s(num_a), "b": s(num_b), "(not set)": s(num_u)}},
        },
        denominators={"m_ret": "m_den"}, numerators={"m_ret": "m_num"}, candidate_drivers=["m_num"],
    ))
    exact_ev = sum(num[-n:]) / sum(den[-n:])
    exact_rf = sum(num[-2 * n : -n]) / sum(den[-2 * n : -n])
    assert r.event.actual == pytest.approx(exact_ev, rel=1e-9)
    assert r.event.reference == pytest.approx(exact_rf, rel=1e-9)
    assert r.verdict != "no_unusual_change" and not [f for f in r.drivers if f.status == "implicated"]
    dim = next(d for d in r.dimensions if d.dimension == "dim_a")
    assert dim.kind == "ratio" and dim.coverage == pytest.approx(1.0, abs=1e-6)
    assert dim.top[0].segment == "a"
    text = "\n".join(r.narrative)
    assert "comparison period" in text and "offsetting" in text  # segments against the total are shown too
