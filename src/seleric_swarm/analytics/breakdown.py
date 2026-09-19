"""Segment share and contribution math — behind ``contribution_analysis`` and
``segment_decomposition``.

Pure functions over neutral inputs, same discipline as ``comparison.py``:
callers normalize their own artifacts into ``Segment``s, so this module never
learns an artifact shape and can be unit tested without a store.

The awkward fact this module exists to handle honestly
------------------------------------------------------
``toolsets/semantic.py::drilldown`` runs a parent ``metrics_query`` to get a
``query_id``, then **discards the parent's rows** — only the per-dimension
children are written as evidence. So a contribution denominator has to be the
sum of the children, and that sum is not guaranteed to be the true total:
``drilldown`` skips rows whose value is null, and any server-side residual or
"other" bucket never reaches us.

Shares computed off a children-sum are therefore *shares of the observed
parts*, not shares of the metric. ``shares()`` reports which one it used and
whether it reconciled, so the caller can say so rather than presenting a
possibly-incomplete denominator as exact.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from seleric_swarm.toolsets import policy_config as policy


class Segment(NamedTuple):
    """One dimension value's measurement for one period."""

    label: str
    value: float | None
    ref: Any = None  # opaque caller handle (artifact id); never inspected here


class Share(NamedTuple):
    label: str
    value: float
    share: float
    ref: Any = None


class ShareResult(NamedTuple):
    shares: list[Share]
    total: float
    #: True when an independently-supplied total agreed with the children sum.
    #: None when no independent total was supplied — unknown, not "fine".
    reconciled: bool | None
    other_count: int
    other_value: float


class Contribution(NamedTuple):
    """One segment's part in an overall period-over-period change."""

    label: str
    value_a: float
    value_b: float
    delta: float
    #: delta_i / total_delta. Can exceed 1.0 or go negative when segments move
    #: in opposite directions — that is real signal, not an error, and is not
    #: clamped.
    share_of_change: float
    ref_a: Any = None
    ref_b: Any = None


def shares(
    segments: list[Segment], *, independent_total: float | None = None
) -> ShareResult | None:
    """Each segment's share of the total. ``None`` when nothing is measurable.

    Segments with a null value are dropped, never zero-filled — an unmeasured
    segment is not a segment worth zero (the same discipline
    ``period_deltas`` and ``models/evaluation.py`` use).

    Small segments below ``MIN_SEGMENT_SHARE`` are folded into an explicit
    "other" count rather than listed: a 200-row channel breakdown is a table,
    not a finding. They stay in the denominator.
    """
    measured = [s for s in segments if s.value is not None]
    if not measured:
        return None

    children_sum = sum(float(s.value) for s in measured)  # type: ignore[arg-type]
    total = independent_total if independent_total is not None else children_sum

    reconciled: bool | None = None
    if independent_total is not None:
        scale = abs(independent_total) or 1.0
        reconciled = abs(children_sum - independent_total) / scale <= (
            policy.CONTRIBUTION_RECONCILIATION_TOLERANCE
        )

    if total == 0:
        # Every part is zero, or they cancel. Shares are undefined rather than
        # zero — dividing here would produce inf/nan and call it a result.
        return ShareResult(shares=[], total=0.0, reconciled=reconciled, other_count=0, other_value=0.0)

    ranked = sorted(measured, key=lambda s: abs(float(s.value)), reverse=True)  # type: ignore[arg-type]
    kept: list[Share] = []
    other_count = 0
    other_value = 0.0
    for segment in ranked:
        value = float(segment.value)  # type: ignore[arg-type]
        share = value / total
        if abs(share) < policy.MIN_SEGMENT_SHARE or len(kept) >= policy.MAX_SEGMENTS_REPORTED:
            other_count += 1
            other_value += value
            continue
        kept.append(Share(label=segment.label, value=value, share=share, ref=segment.ref))

    return ShareResult(
        shares=kept,
        total=total,
        reconciled=reconciled,
        other_count=other_count,
        other_value=other_value,
    )


def contributions(
    period_a: list[Segment], period_b: list[Segment]
) -> tuple[list[Contribution], float]:
    """Each segment's contribution to the total change, plus that total change.

    Delta is ``a - b``, matching ``comparison.period_deltas`` — flipping the
    sign here would make contribution disagree with the comparison tool about
    which direction a metric moved.

    A segment present in only one period is skipped rather than treated as a
    move from zero. "This channel is new" and "this channel went from 0 to N"
    are different claims, and the evidence cannot distinguish them.
    """
    b_by_label = {s.label: s for s in period_b if s.value is not None}
    paired = [
        (a, b_by_label[a.label])
        for a in period_a
        if a.value is not None and a.label in b_by_label
    ]
    if not paired:
        return [], 0.0

    total_delta = sum(float(a.value) - float(b.value) for a, b in paired)  # type: ignore[arg-type]

    out: list[Contribution] = []
    for a, b in paired:
        delta = float(a.value) - float(b.value)  # type: ignore[arg-type]
        # A zero total change with non-zero parts means the segments exactly
        # offset. Share-of-change is genuinely undefined there, so report 0.0
        # for it rather than dividing by zero; the per-segment deltas still
        # carry the real story and the caller warns.
        share_of_change = (delta / total_delta) if total_delta else 0.0
        out.append(
            Contribution(
                label=a.label,
                value_a=float(a.value),  # type: ignore[arg-type]
                value_b=float(b.value),  # type: ignore[arg-type]
                delta=delta,
                share_of_change=share_of_change,
                ref_a=a.ref,
                ref_b=b.ref,
            )
        )

    out.sort(key=lambda c: abs(c.delta), reverse=True)
    return out, total_delta
