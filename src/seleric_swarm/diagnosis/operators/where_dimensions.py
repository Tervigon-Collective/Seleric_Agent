"""Segment localisation for an additive metric and the shared segment helpers: specificity of a change against the
specificity a normal day shows, identifier-like dimensions, coverage of the whole (moved from ``causal/diagnosis.py``,
plan P2.2)."""

from __future__ import annotations

import math
from datetime import date

from seleric_swarm.diagnosis.detect.seasonal import _abs_noise
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.numeric import Series, _finite, _mean, _median, _sign
from seleric_swarm.diagnosis.domain.report import DimensionFinding, SegmentMove
from seleric_swarm.diagnosis.domain.window import EventWindow


def _specificity(event: dict[str, float], ref: dict[str, float]) -> float | None:
    """Share of the change NOT explained by every segment scaling proportionally.

    0 = all segments moved by the same percentage (broad-based); 1 = the change
    sits entirely in segments whose share of the total moved.
    """
    keys = set(event) | set(ref)
    ev_total = sum(event.get(k, 0.0) for k in keys)
    rf_total = sum(ref.get(k, 0.0) for k in keys)
    delta = ev_total - rf_total
    if abs(rf_total) < 1e-12:
        return None
    denom = sum(abs(event.get(k, 0.0) - ref.get(k, 0.0)) for k in keys)
    if denom < 1e-12:
        return 0.0
    dev = sum(abs((event.get(k, 0.0) - ref.get(k, 0.0)) - ref.get(k, 0.0) / rf_total * delta) for k in keys)
    return min(1.0, dev / denom)


def _segment_values(seg: dict[str, Series], days: list[date]) -> dict[str, float]:
    return {s: sum(float(v.get(d, 0.0)) for d in days if _finite(v.get(d, 0.0))) for s, v in seg.items()}


def _segment_ref(seg: dict[str, Series], window: EventWindow) -> dict[str, float]:
    out: dict[str, float] = {}
    for s, v in seg.items():
        total = 0.0
        for d in window.event_days:
            refs = window.reference[d]
            total += _mean([float(v.get(r, 0.0)) * window.factor(d, r) for r in refs]) if refs else 0.0
        out[s] = total
    return out


def _typical_specificity(seg: dict[str, Series], window: EventWindow) -> float | None:
    """Specificity a normal day shows: each reference day vs the other references."""
    vals: list[float] = []
    for d in window.event_days:
        refs = window.reference[d]
        for r in refs:
            others = [o for o in refs if o != r]
            if not others:
                continue
            ev = {s: float(v.get(r, 0.0)) for s, v in seg.items()}
            rf = {s: _mean([float(v.get(o, 0.0)) for o in others]) for s, v in seg.items()}
            sp = _specificity(ev, rf)
            if sp is not None:
                vals.append(sp)
    return _median(vals) if vals else None


def _is_identifier_like(seg: dict[str, Series]) -> bool:
    """A dimension whose values almost never repeat across days (ids, timestamps)."""
    if not seg:
        return True
    days_present = [sum(1 for v in s.values() if _finite(v) and v != 0) for s in seg.values()]
    return _median([float(x) for x in days_present]) < 2


def _analyse_additive_dimension(
    dim: str, seg: dict[str, Series], window: EventWindow, total_delta: float, total_reference: float,
    same_weekday: bool = True, *, cfg: DiagnosisConfig,
) -> DimensionFinding | None:
    if _is_identifier_like(seg):
        return None
    ev = _segment_values(seg, window.event_days)
    rf = _segment_ref(seg, window)
    keys = sorted(set(ev) | set(rf))
    seg_delta = sum(ev.get(k, 0.0) - rf.get(k, 0.0) for k in keys)
    rf_total = sum(abs(v) for v in rf.values()) or 1.0
    n_ev = len(window.event_days)
    moves: list[SegmentMove] = []
    for k in keys:
        d = ev.get(k, 0.0) - rf.get(k, 0.0)
        noise = _abs_noise(seg.get(k, {}), window.history_days, set(window.excluded_outliers), same_weekday, cfg=cfg)
        z = d / (noise * math.sqrt(n_ev)) if noise else None
        moves.append(SegmentMove(
            segment=k, event=ev.get(k, 0.0), reference=rf.get(k, 0.0), delta=d,
            share_of_change=(d / total_delta) if abs(total_delta) > 1e-12 else None,
            z_score=z, significant=(z is not None and abs(z) >= cfg.event_z),
        ))
    moves.sort(key=lambda m: -abs(m.delta))
    spec = _specificity(ev, rf)
    typical = _typical_specificity(seg, window)
    # Localised: segments that moved beyond their own noise, in the event's
    # direction, carry most of the change while holding a minority of volume.
    hot = [m for m in moves if m.significant and _sign(m.delta) == _sign(total_delta)]
    hot_share = sum(m.delta for m in hot) / total_delta if abs(total_delta) > 1e-12 else 0.0
    hot_mass = sum(abs(rf.get(m.segment, 0.0)) for m in hot) / rf_total
    localised = bool(hot) and hot_share >= 0.5 and hot_mass < 0.5
    broad = not localised and spec is not None and spec <= cfg.broad_based_max
    note = ""
    if localised:
        note = f"{len(hot)} segment(s) holding {hot_mass:.0%} of normal volume carry {hot_share:.0%} of the change"
    return DimensionFinding(
        dimension=dim, kind="additive", n_segments=len(keys),
        coverage=(seg_delta / total_delta) if abs(total_delta) > 1e-12 else None,
        specificity=spec, typical_specificity=typical, localised=localised, broad_based=broad,
        top=moves[: cfg.max_segments_reported], note=note,
        volume_coverage=(sum(rf.values()) / total_reference) if abs(total_reference) > 1e-12 else None,
        hot_share=hot_share,
    )


def _rate_coverage(w0: dict[str, float], total_weight: Series | None, window: EventWindow) -> float | None:
    if not total_weight:
        return None
    ref_total = 0.0
    for d in window.event_days:
        vals = [float(total_weight[r]) for r in window.reference[d] if _finite(total_weight.get(r))]
        if not vals:
            return None
        ref_total += _mean(vals)
    return sum(w0.values()) / ref_total if ref_total > 0 else None


def _covers_whole(d: DimensionFinding, *, cfg: DiagnosisConfig) -> bool:
    """Segments that add back up to the metric: attribution-only fields cover a
    slice (too little); a dimension from a finer breakdown table counts each
    unit once per breakdown row (too much)."""
    lo = cfg.min_dimension_coverage
    return d.volume_coverage is None or lo <= d.volume_coverage <= 1.0 / lo
