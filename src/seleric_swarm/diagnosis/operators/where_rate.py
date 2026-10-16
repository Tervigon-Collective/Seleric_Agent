"""Segment localisation for a rate or a ratio: the change splits into a mix effect and a within-segment rate effect, which
is where Simpson's paradox shows (moved from ``causal/diagnosis.py``, plan P2.2)."""

from __future__ import annotations

import math
from datetime import date

from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.numeric import Series, _finite, _mean, _median, _sign
from seleric_swarm.diagnosis.domain.report import DimensionFinding, SegmentMove
from seleric_swarm.diagnosis.domain.window import EventWindow
from seleric_swarm.diagnosis.operators.where_dimensions import (
    _is_identifier_like,
    _rate_coverage,
    _specificity,
)


def _analyse_rate_dimension(
    dim: str, rate: dict[str, Series], weight: dict[str, Series], window: EventWindow, total_rate: Series,
    total_weight: Series | None = None, *, cfg: DiagnosisConfig,
) -> DimensionFinding | None:
    """Mix/rate split of a weighted-average metric across one dimension."""
    if _is_identifier_like(weight):
        return None
    segs = sorted(set(rate) & set(weight))
    if not segs:
        return None
    # Verify the weighted-average identity on reference days before trusting it.
    check: list[float] = []
    for d in {r for refs in window.reference.values() for r in refs}:
        w = {s: float(weight[s].get(d, 0.0)) for s in segs}
        wt = sum(w.values())
        if wt <= 0 or d not in total_rate:
            continue
        implied = sum(w[s] * float(rate[s].get(d, 0.0)) for s in segs) / wt
        if abs(total_rate[d]) > 1e-12:
            check.append(abs(implied - total_rate[d]) / abs(total_rate[d]))
    if not check or _median(check) > 5 * cfg.identity_tolerance:
        return None

    def agg(days_by_event: dict[date, list[date]] | None) -> tuple[dict[str, float], dict[str, float]]:
        w_out: dict[str, float] = {}
        n_out: dict[str, float] = {}
        for s in segs:
            w_sum = 0.0
            n_sum = 0.0
            for d in window.event_days:
                days = [d] if days_by_event is None else days_by_event[d]
                if not days:
                    continue
                fac = [1.0 if days_by_event is None else window.factor(d, x) for x in days]
                # The outcome's level factor scales the rate, so it rides on the
                # numerator only; weights (the volume mix) stay as observed.
                ws = [float(weight[s].get(x, 0.0)) for x in days]
                ns = [float(weight[s].get(x, 0.0)) * float(rate[s].get(x, 0.0)) * f for x, f in zip(days, fac, strict=True)]
                w_sum += _mean(ws)
                n_sum += _mean(ns)
            w_out[s] = w_sum
            n_out[s] = n_sum
        return w_out, n_out

    w1, n1 = agg(None)
    w0, n0 = agg(window.reference)
    W1, W0 = sum(w1.values()), sum(w0.values())
    if W1 <= 0 or W0 <= 0:
        return None
    r1 = {s: n1[s] / w1[s] if w1[s] > 0 else 0.0 for s in segs}
    r0 = {s: n0[s] / w0[s] if w0[s] > 0 else 0.0 for s in segs}
    s1 = {s: w1[s] / W1 for s in segs}
    s0 = {s: w0[s] / W0 for s in segs}
    R1 = sum(s1[s] * r1[s] for s in segs)
    R0 = sum(s0[s] * r0[s] for s in segs)
    dR = R1 - R0
    proportion = all(0.0 <= r <= 1.0 for r in (*r0.values(), *r1.values()))
    moves: list[SegmentMove] = []
    mix_total = rate_total = 0.0
    for s in segs:
        # Mix is priced against the overall reference rate: shares sum to one, so
        # the totals are unchanged, but a segment that GAINED volume at a low rate
        # carries the mix effect, not the segments whose share it took (live
        # 2026-10-04: an awareness campaign with ~0 clicks took half the
        # impressions; the plain (r0+r1)/2 form pinned the fall on the sales
        # campaigns, whose rate had held).
        mix = (s1[s] - s0[s]) * ((r0[s] + r1[s]) / 2.0 - R0)
        rate_eff = (r1[s] - r0[s]) * (s0[s] + s1[s]) / 2.0
        mix_total += mix
        rate_total += rate_eff
        z = None
        sig = None
        if proportion and w1[s] > 0 and w0[s] > 0:
            pooled = (n1[s] + n0[s]) / (w1[s] + w0[s])
            se = math.sqrt(max(pooled * (1 - pooled), 0.0) * (1 / w1[s] + 1 / w0[s]))
            z = (r1[s] - r0[s]) / se if se > 0 else None
            sig = z is not None and abs(z) >= cfg.event_z
        moves.append(SegmentMove(
            segment=s, event=w1[s], reference=w0[s], delta=w1[s] - w0[s],
            share_of_change=((mix + rate_eff) / dR) if abs(dR) > 1e-12 else None,
            rate_event=r1[s], rate_reference=r0[s], rate_effect=rate_eff, mix_effect=mix,
            z_score=z, significant=sig, volume_share_event=s1[s], volume_share_reference=s0[s],
        ))
    moves.sort(key=lambda m: -abs((m.rate_effect or 0.0) + (m.mix_effect or 0.0)))
    # Simpson: the aggregate moves one way while the within-segment rates move
    # the other (or not at all) and the shift in mix carries the change.
    simpson = _sign(dR) != 0 and _sign(rate_total) != _sign(dR) and abs(mix_total) > abs(rate_total)
    spec_ev = {s: n1[s] for s in segs}
    spec_rf = {s: n0[s] for s in segs}
    spec = _specificity(spec_ev, spec_rf)
    hot = [m for m in moves if m.significant and _sign((m.rate_effect or 0) + (m.mix_effect or 0)) == _sign(dR)]
    hot_share = sum((m.rate_effect or 0) + (m.mix_effect or 0) for m in hot) / dR if abs(dR) > 1e-12 else 0.0
    hot_mass = sum(s0[m.segment] for m in hot)
    rate_localised = bool(hot) and hot_share >= 0.5 and hot_mass < 0.5
    # Mix-driven: the shift in volume between segments carries most of the change
    # while the rates within them held (Simpson's paradox is the special case
    # where they moved the other way).
    mix_driven = (
        not simpson and abs(dR) > 1e-12 and _sign(mix_total) == _sign(dR)
        and abs(mix_total) >= 0.5 * abs(dR) and abs(mix_total) > abs(rate_total)
    )
    note = ""
    if mix_driven:
        note = "the overall rate moved mainly because the mix shifted between segments; rates within them held"
    if rate_localised:
        note = (
            f"{len(hot)} segment(s) holding {hot_mass:.0%} of the volume carry {hot_share:.0%} of the change "
            "through their own rate"
        )
    if simpson:
        note = (
            "Simpson's paradox: the overall rate moved because the mix shifted toward "
            "segments with different rates, while the rates within segments moved the other way or held."
        )
    return DimensionFinding(
        dimension=dim, kind="rate", n_segments=len(segs), coverage=((mix_total + rate_total) / dR) if abs(dR) > 1e-12 else None,
        specificity=spec, typical_specificity=None, localised=simpson or rate_localised or mix_driven,
        broad_based=not (simpson or rate_localised or mix_driven) and spec is not None and spec <= cfg.broad_based_max,
        top=moves[: cfg.max_segments_reported], mix_effect=mix_total, rate_effect=rate_total,
        simpsons_paradox=simpson, note=note,
        volume_coverage=_rate_coverage(w0, total_weight, window),
        hot_share=hot_share,
    )


def _analyse_ratio_dimension(
    dim: str, num: dict[str, Series], den: dict[str, Series], window: EventWindow, total_rate: Series,
    total_den: Series | None = None, *, cfg: DiagnosisConfig,
) -> DimensionFinding | None:
    """Exact split of a ratio of two sums (R = N / D) across one dimension.

    A weighted average of segment rates only rebuilds R when every unit of N
    sits in a segment with some D; sales on a campaign with no spend in the
    window break it, and the dimension used to vanish from the diagnosis. The
    ratio-of-sums split always holds: with R0 = N0 / D0,
    R1 - R0 = sum_s (dN_s - R0 * dD_s) / D1, so each segment contributes what
    it added to N beyond the reference return on what it added to D.
    """
    if _is_identifier_like(den) and _is_identifier_like(num):
        return None
    segs = sorted(set(num) | set(den))
    if len(segs) < 2:
        return None
    # The split must rebuild the reported ratio on reference days before it is trusted.
    check: list[float] = []
    for d in {r for refs in window.reference.values() for r in refs}:
        n_t = sum(float(num.get(s, {}).get(d, 0.0)) for s in segs)
        d_t = sum(float(den.get(s, {}).get(d, 0.0)) for s in segs)
        if d_t > 0 and _finite(total_rate.get(d)) and abs(total_rate[d]) > 1e-12:
            check.append(abs(n_t / d_t - total_rate[d]) / abs(total_rate[d]))
    if not check or _median(check) > 5 * cfg.identity_tolerance:
        return None

    def agg(series: dict[str, Series], scaled: bool) -> tuple[dict[str, float], dict[str, float]]:
        ev: dict[str, float] = {}
        rf: dict[str, float] = {}
        for s in segs:
            v = series.get(s, {})
            ev[s] = sum(float(v.get(d, 0.0)) for d in window.event_days)
            # The outcome's level factor scales the ratio, so it rides on N only.
            rf[s] = sum(
                _mean([float(v.get(r, 0.0)) * (window.factor(d, r) if scaled else 1.0) for r in window.reference[d]])
                if window.reference[d] else 0.0
                for d in window.event_days
            )
        return ev, rf

    n1, n0 = agg(num, True)
    d1, d0 = agg(den, False)
    N1, N0, D1, D0 = sum(n1.values()), sum(n0.values()), sum(d1.values()), sum(d0.values())
    if D1 <= 0 or D0 <= 0:
        return None
    R0, R1 = N0 / D0, N1 / D1
    dR = R1 - R0
    moves: list[SegmentMove] = []
    num_total = den_total = 0.0
    for s in segs:
        num_eff = (n1[s] - n0[s]) / D1
        den_eff = -R0 * (d1[s] - d0[s]) / D1
        num_total += num_eff
        den_total += den_eff
        moves.append(SegmentMove(
            segment=s, event=d1[s], reference=d0[s], delta=d1[s] - d0[s],
            share_of_change=((num_eff + den_eff) / dR) if abs(dR) > 1e-12 else None,
            rate_event=(n1[s] / d1[s]) if d1[s] > 0 else None, rate_reference=(n0[s] / d0[s]) if d0[s] > 0 else None,
            rate_effect=num_eff, mix_effect=den_eff,
            volume_share_event=d1[s] / D1, volume_share_reference=d0[s] / D0,
            numerator_event=n1[s], numerator_reference=n0[s],
        ))
    moves.sort(key=lambda m: -abs((m.rate_effect or 0.0) + (m.mix_effect or 0.0)))
    # Localised: the fewest segments (largest first) that carry half the change,
    # in its direction, hold a minority of the reference volume.
    hot: list[SegmentMove] = []
    carried = 0.0
    for m in moves:
        c = (m.rate_effect or 0.0) + (m.mix_effect or 0.0)
        if abs(dR) <= 1e-12 or _sign(c) != _sign(dR) or carried / dR >= 0.5:
            break
        hot.append(m)
        carried += c
    hot_share = carried / dR if abs(dR) > 1e-12 else 0.0
    hot_mass = sum(abs(m.reference) for m in hot) / (sum(abs(v) for v in d0.values()) or 1.0)
    localised = bool(hot) and hot_share >= 0.5 and hot_mass < 0.5
    note = (
        f"{len(hot)} segment(s) holding {hot_mass:.0%} of the reference volume carry {hot_share:.0%} of the change"
        if localised else ""
    )
    spec = _specificity({s: n1[s] for s in segs}, {s: n0[s] for s in segs})
    return DimensionFinding(
        dimension=dim, kind="ratio", n_segments=len(segs),
        coverage=((num_total + den_total) / dR) if abs(dR) > 1e-12 else None,
        specificity=spec, typical_specificity=None, localised=localised,
        broad_based=not localised and spec is not None and spec <= cfg.broad_based_max,
        top=moves[: cfg.max_segments_reported], mix_effect=den_total, rate_effect=num_total,
        note=note, volume_coverage=_rate_coverage(d0, total_den, window), hot_share=hot_share,
    )
