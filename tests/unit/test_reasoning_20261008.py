"""Comparison direction, period-to-date spans and finding-backed figures (2026-10-08).

Live MS3-18085c0092 ("this month vs the same days last month") and MS3-f23af5a954
("this week compared to last week"): the windows arrive in the order the question
names them, the executor measured the earlier window against the later one, and
the answer said net sales "fell from 696,778 to 767,584"."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.services import elapsed
from tests.unit.test_planner_executor_context import IST

_PER_DAY = 100.0
_PER_HOUR = 10.0


class _DailyMcp:
    """``spend`` is 100 a day (10 an hour); ``ret`` is the day count of the window."""

    def __init__(self) -> None:
        self.ranges: list[tuple[str, str, str | None]] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict) -> dict:
        measure = arguments["measures"][0]
        start = date.fromisoformat(arguments["time_range"]["start"])
        end = date.fromisoformat(arguments["time_range"]["end"])
        self.ranges.append((str(start), str(end), arguments.get("granularity")))
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        if arguments.get("granularity") == "hour":
            rows = [
                {"x.hour": f"{d}T{h:02d}:00:00.000", measure: str(_PER_HOUR)} for d in days for h in range(24)
            ]
            return {"rows": rows, "provenance": {"query_id": "q"}}
        value = len(days) * _PER_DAY if measure == "spend" else float(len(days))
        return {"rows": [{measure: str(value)}], "provenance": {"query_id": "q"}}








def _artifact(kind: str, payload: dict, evidence_ids: list[str] | None = None) -> Artifact:
    return Artifact(
        workspace_id="w", artifact_type=kind, payload=payload, classification="factual",
        evidence_ids=evidence_ids or ["raw"], provenance=ArtifactProvenance(), mission_id="MS3-x",
    )




def test_the_same_span_is_cut_only_for_a_running_period_to_date(monkeypatch) -> None:
    monkeypatch.setattr(elapsed, "now_in", lambda tz: datetime(2026, 10, 8, 16, tzinfo=IST))
    as_of = datetime(2026, 10, 8, tzinfo=IST)
    this_week, last_week = (date(2026, 10, 5), date(2026, 10, 8)), (date(2026, 9, 28), date(2026, 10, 4))
    assert elapsed.same_span(last_week, this_week, as_of) == (date(2026, 9, 28), date(2026, 10, 1))
    # Today against the days before compares per day over the same hours instead.
    assert elapsed.same_span(last_week, (date(2026, 10, 8), date(2026, 10, 8)), as_of) is None
    # Two complete windows compare as they are.
    assert elapsed.same_span((date(2026, 9, 21), date(2026, 9, 27)), last_week, as_of) is None










def test_a_stated_hour_caps_the_same_hours_cut(monkeypatch) -> None:
    """"till 11 am" asked at 15:00 compares 00:00-11:00 (live 2026-10-09 MS3-0f473daefd)."""
    monkeypatch.setattr(elapsed, "now_in", lambda tz: datetime(2026, 10, 9, 15, 5, tzinfo=IST))
    as_of = datetime(2026, 10, 9, tzinfo=IST)
    try:
        elapsed.state_cutoff_hour(11)
        assert elapsed.completed_hours(as_of) == 11
        elapsed.state_cutoff_hour(18)  # a stated hour still to come cannot reach past now
        assert elapsed.completed_hours(as_of) == 15
    finally:
        elapsed.state_cutoff_hour(None)
    assert elapsed.completed_hours(as_of) == 15


def test_a_diagnosis_window_keeps_its_last_day() -> None:
    """The pinned "last week" ends at 10-04T00:00 and means 10-04 inclusive (live
    2026-10-09 MS3-2ec0e4a115 diagnosed 09-28..10-03)."""
    from seleric_swarm.toolsets.diagnosis import _inclusive_days

    start, end = datetime(2026, 9, 28, tzinfo=IST), datetime(2026, 10, 4, tzinfo=IST)
    assert _inclusive_days(start, end) == (date(2026, 9, 28), date(2026, 10, 4))




