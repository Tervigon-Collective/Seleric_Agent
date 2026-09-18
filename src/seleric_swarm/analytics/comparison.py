"""Period-over-period comparison — the arithmetic behind ``compare_periods``.

Extracted in place from
``swarm/specialists/observer.py::_post_comparison_deltas`` (Sprint 1 step 1,
``03_PROFILE_CAPABILITIES.md`` §10). That function still owns its Blackboard
writes and keeps its signature; only the pairing and subtraction moved here,
so the existing tests staying green is the proof the math did not drift
rather than a reviewer's eye.

Callers normalize their own rows into ``MetricPoint``s, which is what lets
one implementation serve both the legacy Blackboard ``Evidence`` dicts
(``metric_or_fact``) and the V3 ``EvidenceArtifact``s (``metric_id``)
without this module knowing either shape.
"""

from __future__ import annotations

from typing import Any, NamedTuple


class MetricPoint(NamedTuple):
    """One metric's value for one period, ready to pair against another.

    ``ref`` is an opaque caller handle — a Blackboard row, an artifact id,
    whatever the caller needs back after pairing. This module never inspects
    it, which is how the pure function stays ignorant of storage.
    """

    metric: str
    dimensions: dict[str, Any]
    value: float | None
    ref: Any = None


class PeriodDelta(NamedTuple):
    metric: str
    dimensions: dict[str, Any]
    delta: float
    a: MetricPoint
    b: MetricPoint


def pair_key(metric: str, dimensions: dict[str, Any] | None) -> tuple[str, tuple[tuple[str, Any], ...]]:
    """Identity a metric is paired on across two periods: name + its dimension slice."""
    return (metric, tuple(sorted((dimensions or {}).items())))


def period_deltas(points_a: list[MetricPoint], points_b: list[MetricPoint]) -> list[PeriodDelta]:
    """One delta per (metric, dimensions) pair present in *both* periods.

    Delta is always ``a - b`` — the order the question named the periods in,
    so a decline reads negative regardless of which period is
    chronologically earlier. This matches the lookup pipeline's convention
    (``services/intelligence/observer.py::_comparison_deltas``); changing the
    sign here would silently flip the direction of every comparison finding.

    A pair is skipped, never zero-filled, when the metric is missing from one
    period or either value is ``None`` — a missing measurement is not a
    measurement of zero (``docs/BUG_SHEET.md`` #7's "never fabricate"
    discipline).

    On duplicate keys within one period the last point wins, in *both*
    periods — the code this replaced keyed each period into a dict before
    pairing, so a repeated (metric, dimensions) yielded one delta, not one
    per occurrence. Iterating ``points_a`` directly here would have changed
    that; don't.
    """
    a_by_key = {pair_key(p.metric, p.dimensions): p for p in points_a}
    b_by_key = {pair_key(p.metric, p.dimensions): p for p in points_b}
    out: list[PeriodDelta] = []
    for key, point_a in a_by_key.items():
        point_b = b_by_key.get(key)
        if point_b is None or point_a.value is None or point_b.value is None:
            continue
        out.append(
            PeriodDelta(
                metric=point_a.metric,
                dimensions=dict(point_a.dimensions or {}),
                delta=float(point_a.value) - float(point_b.value),
                a=point_a,
                b=point_b,
            )
        )
    return out
