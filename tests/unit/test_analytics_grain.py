"""Unit tests for analytics/grain.py — the A1.2 precondition.

``docs/refactor/CONTRACTS.md`` amendment A1.2. These are pure-function tests:
no artifact store, no RunContext, no event loop. The toolset-level behavior
(that a violation becomes ``ToolResult(success=False,
error_code="EVIDENCE_GRAIN_MISMATCH")``) is covered in
``test_analytics_toolset.py``.

Why this layer is tested separately: the grain rule is what replaces the
``granularity`` thread swarm_v2 runs through mission state
(``graph.py:1194`` -> ``observer.py:42``), which the V3 architecture removes.
If this logic is wrong, ``docs/BUG_SHEET.md`` #14 comes back as an
intermittent rather than a failing test.
"""

from __future__ import annotations

from datetime import UTC, datetime

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.analytics.grain import period_span_days, validate_grain_set


def _ev(
    *,
    metric: str = "metric.net_sales",
    grain: str = "day",
    start: str,
    end: str,
    value: float = 1.0,
) -> EvidenceArtifact:
    return EvidenceArtifact(
        metric_id=metric,
        grain=grain,  # type: ignore[arg-type]
        as_of=datetime(2026, 9, 17, tzinfo=UTC),
        period_start=datetime.fromisoformat(start).replace(tzinfo=UTC),
        period_end=datetime.fromisoformat(end).replace(tzinfo=UTC),
        value=value,
        source_query={},
    )


def test_period_span_is_inclusive_so_one_day_is_one():
    """Matches observer.py::_daily_windows (start == end per day) and
    anomaly.py's ``(end - start).days + 1`` -- a single day is span 1."""
    assert period_span_days(_ev(start="2026-09-15", end="2026-09-15").period_start,
                            _ev(start="2026-09-15", end="2026-09-15").period_end) == 1


def test_period_span_counts_both_endpoints():
    ev = _ev(start="2026-09-12", end="2026-09-16")
    assert period_span_days(ev.period_start, ev.period_end) == 5


def test_empty_set_is_not_a_grain_problem():
    """An empty set is the caller's INSUFFICIENT_EVIDENCE case. Reporting it
    as a grain mismatch would send the agent chasing the wrong fix."""
    assert validate_grain_set([]) is None


def test_clean_daily_series_passes():
    series = [_ev(start=f"2026-09-1{d}", end=f"2026-09-1{d}") for d in range(2, 7)]
    assert validate_grain_set(series) is None


def test_multi_day_window_labelled_day_grain_is_rejected():
    """docs/BUG_SHEET.md #14's exact shape: a window aggregate wearing a
    daily label. The old code divided it by the day count; the new contract
    refuses it, because a 5-day sum / 5 is not a real daily value."""
    reason = validate_grain_set([_ev(grain="day", start="2026-09-12", end="2026-09-16")])
    assert reason is not None
    assert "spans 5 day(s)" in reason
    assert "window aggregate" in reason


def test_mixed_grains_in_one_call_are_rejected():
    reason = validate_grain_set(
        [
            _ev(grain="day", start="2026-09-15", end="2026-09-15"),
            _ev(grain="month", start="2026-08-01", end="2026-08-31"),
        ]
    )
    assert reason is not None
    assert "mixes grains" in reason


def test_unequal_spans_at_grain_none_are_rejected():
    """grain="none" skips the per-artifact span check (no declared grain to
    contradict), but an observation still can't be scored against a baseline
    covering a different number of days."""
    reason = validate_grain_set(
        [
            _ev(grain="none", start="2026-09-01", end="2026-09-07"),
            _ev(grain="none", start="2026-09-08", end="2026-09-30"),
        ]
    )
    assert reason is not None
    assert "compared against a baseline covering the same number of days" in reason


def test_equal_spans_at_grain_none_pass():
    assert (
        validate_grain_set(
            [
                _ev(grain="none", start="2026-09-01", end="2026-09-07"),
                _ev(grain="none", start="2026-09-08", end="2026-09-14"),
            ]
        )
        is None
    )


def test_week_grain_requires_seven_days():
    assert validate_grain_set([_ev(grain="week", start="2026-09-07", end="2026-09-13")]) is None
    reason = validate_grain_set([_ev(grain="week", start="2026-09-07", end="2026-09-10")])
    assert reason is not None and "expected 7" in reason


def test_month_grain_accepts_real_calendar_month_lengths():
    """28-31 days, because calendar months genuinely differ -- February and
    August must both pass without a special case at the call site."""
    assert validate_grain_set([_ev(grain="month", start="2026-02-01", end="2026-02-28")]) is None
    assert validate_grain_set([_ev(grain="month", start="2026-08-01", end="2026-08-31")]) is None
    reason = validate_grain_set([_ev(grain="month", start="2026-08-01", end="2026-09-30")])
    assert reason is not None and "expected 28-31" in reason


def test_comparable_months_of_different_lengths_are_still_rejected():
    """Deliberate, and a judgment call worth seeing in a test: Feb (28) vs
    Aug (31) are both valid months, but scoring one against the other
    compares unequal windows. Rule 3 refuses it rather than silently
    accepting a 10% window-size difference as signal."""
    reason = validate_grain_set(
        [
            _ev(grain="month", start="2026-02-01", end="2026-02-28"),
            _ev(grain="month", start="2026-08-01", end="2026-08-31"),
        ]
    )
    assert reason is not None
    assert "same number of days" in reason
