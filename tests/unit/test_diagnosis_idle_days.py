"""An additive series has no row on an idle day: that is a 0, not a gap (live 2026-10-21 MS3-7100f30301)."""

from __future__ import annotations

from datetime import date

from seleric_swarm.toolsets.diagnosis import _fill_idle_days

TODAY = date(2026, 10, 21)


def test_idle_days_inside_the_window_become_zero_so_the_mean_runs_over_every_day() -> None:
    sparse = {date(2026, 10, 14): 1100.84, date(2026, 10, 16): 0.0, date(2026, 10, 19): 1100.85}
    filled = _fill_idle_days(sparse, date(2026, 10, 20), TODAY)
    window = [date(2026, 10, d) for d in range(14, 21)]
    assert [filled[d] for d in window] == [1100.84, 0.0, 0.0, 0.0, 0.0, 1100.85, 0.0]
    assert round(sum(filled[d] for d in window) / len(window)) == 315  # not 2201.69 / 3 = 734


def test_days_before_the_first_row_and_the_running_day_are_not_invented() -> None:
    filled = _fill_idle_days({date(2026, 10, 18): 5.0}, date(2026, 10, 21), TODAY)
    assert date(2026, 10, 17) not in filled and date(2026, 10, 21) not in filled
    assert filled[date(2026, 10, 20)] == 0.0


def test_empty_series_stays_empty() -> None:
    assert _fill_idle_days({}, date(2026, 10, 20), TODAY) == {}
