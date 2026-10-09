"""Comparison direction, period-to-date spans and finding-backed figures (2026-10-08).

Live MS3-18085c0092 ("this month vs the same days last month") and MS3-f23af5a954
("this week compared to last week"): the windows arrive in the order the question
names them, the executor measured the earlier window against the later one, and
the answer said net sales "fell from 696,778 to 767,584"."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from seleric_swarm.agent import executor
from seleric_swarm.agent.artifacts import EvidenceArtifact, Finding
from seleric_swarm.agent.output import MissionResult
from seleric_swarm.agent.plan import MissionPlan, PlanStep
from seleric_swarm.agent.validation.grounding import check_answer_grounding
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.services import elapsed
from tests.unit.test_planner_executor_context import IST, TODAY, _deps

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


def _period_plan() -> MissionPlan:
    return MissionPlan(
        shape="period_comparison",
        steps=[PlanStep(tool="query_metrics", metric_ids=["spend", "ret"], purpose="compare")],
    )


@pytest.mark.asyncio
async def test_the_change_runs_from_the_earlier_window_whatever_order_the_question_names(monkeypatch) -> None:
    monkeypatch.setattr(elapsed, "now_in", lambda tz: datetime(2026, 10, 8, 9, tzinfo=IST))  # TODAY is complete
    deps = _deps(_DailyMcp())
    later_first = [(date(2026, 10, 3), date(2026, 10, 6)), (date(2026, 9, 29), date(2026, 9, 30))]
    out = await executor.execute_plan(_period_plan(), deps, windows=later_first, as_of=TODAY)
    assert out is not None
    # 2 days (200) before, 4 days (400) after: per day 100 -> 100; the ratio 2 -> 4 is +100%.
    assert "| metric | 2026-09-29..2026-09-30 (per day) | 2026-10-03..2026-10-06 (per day) | change |" in out.text
    assert "| ret | 2.00 | 4.00 | +100.0% |" in out.text
    finding = Finding.model_validate(deps.artifact_store.get(out.finding_id).payload)
    assert finding.metrics["ret | change_pct"] == 100.0


@pytest.mark.asyncio
async def test_a_period_to_date_compares_the_same_span_of_the_period_before(monkeypatch) -> None:
    """This week (Oct 5-7, today at 12:30) vs last week (Sep 28-Oct 4): last week is
    cut to Sep 28-30, Sep 30 counted to 12:00 like today, and totals compare."""
    monkeypatch.setattr(elapsed, "now_in", lambda tz: datetime(2026, 10, 7, 12, 30, tzinfo=IST))
    mcp = _DailyMcp()
    deps = _deps(mcp)
    windows = [(date(2026, 10, 5), date(2026, 10, 7)), (date(2026, 9, 28), date(2026, 10, 4))]
    out = await executor.execute_plan(_period_plan(), deps, windows=windows, as_of=TODAY)
    assert out is not None
    span_total = 2 * _PER_DAY + 12 * _PER_HOUR
    assert f"| spend | {span_total:,.2f} | {span_total:,.2f} | +0.0% |" in out.text
    assert "2026-09-28..2026-09-30 | 2026-10-05..2026-10-07 | change |" in out.text
    assert ("2026-09-28", "2026-09-29", None) in mcp.ranges
    assert ("2026-09-30", "2026-09-30", "hour") in mcp.ranges
    assert ("2026-10-07", "2026-10-07", "hour") in mcp.ranges
    assert "Same span" in out.text


def _artifact(kind: str, payload: dict, evidence_ids: list[str] | None = None) -> Artifact:
    return Artifact(
        workspace_id="w", artifact_type=kind, payload=payload, classification="factual",
        evidence_ids=evidence_ids or ["raw"], provenance=ArtifactProvenance(), mission_id="MS3-x",
    )


def test_a_figure_from_a_finding_shows_the_metric_it_was_computed_from() -> None:
    """The executor's per-day figures stand for the evidence they divide (live
    MS3-f23af5a954: three revisions over per-day ad spend reported exactly)."""
    def ev(metric: str, value: float, start: date, end: date) -> Artifact:
        at = lambda d: datetime(d.year, d.month, d.day, tzinfo=IST)  # noqa: E731
        return _artifact("evidence", EvidenceArtifact(
            metric_id=metric, dimensions={}, grain="none", as_of=TODAY, period_start=at(start),
            period_end=at(end), value=value, source_query={},
        ).model_dump(mode="json"))

    before = ev("spend", 700.0, date(2026, 9, 28), date(2026, 10, 4))
    after = ev("spend", 400.0, date(2026, 10, 5), date(2026, 10, 8))
    finding = _artifact("finding", Finding(
        finding_type="prefetched_comparison", statement="s", evidence_ids=[before.id, after.id],
        metrics={"spend | ref": 100.0, "spend | cmp": 100.0, "spend | change_pct": 0.0},
    ).model_dump(mode="json"), [before.id, after.id])
    result = MissionResult(
        status="completed", final_response="Spend held at 100 a day in both weeks.", finding_ids=[finding.id],
    )
    outcome = check_answer_grounding([before, after, finding], result)
    assert not outcome.gaps, outcome.gaps

    silent = MissionResult(status="completed", final_response="Spend was steady.", finding_ids=[finding.id])
    gap = check_answer_grounding([before, after, finding], silent).gaps[0].description
    # The revision is told what the evidence holds, not just that something is missing.
    assert "700.00 for 2026-09-28..2026-10-04" in gap


def test_the_same_span_is_cut_only_for_a_running_period_to_date(monkeypatch) -> None:
    monkeypatch.setattr(elapsed, "now_in", lambda tz: datetime(2026, 10, 8, 16, tzinfo=IST))
    as_of = datetime(2026, 10, 8, tzinfo=IST)
    this_week, last_week = (date(2026, 10, 5), date(2026, 10, 8)), (date(2026, 9, 28), date(2026, 10, 4))
    assert elapsed.same_span(last_week, this_week, as_of) == (date(2026, 9, 28), date(2026, 10, 1))
    # Today against the days before compares per day over the same hours instead.
    assert elapsed.same_span(last_week, (date(2026, 10, 8), date(2026, 10, 8)), as_of) is None
    # Two complete windows compare as they are.
    assert elapsed.same_span((date(2026, 9, 21), date(2026, 9, 27)), last_week, as_of) is None


def test_rows_fetched_under_different_filters_never_contradict() -> None:
    """Suspender Boots orders by ad vs all orders by ad share labels, not scope (live
    2026-10-08 MS3-f528c2cde3: three revisions over "reported as 15 and 16")."""
    from seleric_swarm.agent.validation.signals import check_contradiction

    def ev(value: float, filters: list[dict]) -> Artifact:
        return _artifact("evidence", EvidenceArtifact(
            metric_id="spend", dimensions={"camp": "A"}, grain="none", as_of=TODAY, period_start=TODAY,
            period_end=TODAY, value=value, source_query={"filters": filters},
        ).model_dump(mode="json"))

    scoped = [{"dimension": "product", "operator": "contains", "values": ["boots"]}]
    assert not check_contradiction([ev(15.0, scoped), ev(16.0, [])]).challenges
    assert check_contradiction([ev(15.0, scoped), ev(30.0, scoped)]).challenges


def test_the_users_own_numbers_are_not_unbacked_figures() -> None:
    from seleric_swarm.agent.validation import _unbacked_figures, _with_query

    deps = _deps(store=None)
    deps.artifact_store.put(_artifact("evidence", EvidenceArtifact(
        metric_id="spend", dimensions={}, grain="none", as_of=TODAY, period_start=TODAY,
        period_end=TODAY, value=120000.0, source_query={},
    ).model_dump(mode="json")))
    answer = MissionResult(status="completed", final_response="Of the extra 50,000 a day, keep 120,000 base spend.")
    unbacked, _share = _unbacked_figures(_with_query(answer, "If I add 50000 INR/day of budget"), deps)
    assert unbacked == []


@pytest.mark.asyncio
async def test_entities_compared_with_no_measure_named_use_the_headline_they_carry() -> None:
    """Live 2026-10-09 MS3-0f473daefd: "compare performance of meta ads campaigns" named no
    measure, the plan was invalid and the answer gave four account totals."""
    from seleric_swarm.agent.plan import PlanSlots, plan_from_slots
    from tests.unit.test_planner_executor_context import _catalogue

    slots = PlanSlots(shape="entity_comparison", entity_dimension="camp")
    windows = [(date(2026, 10, 8), date(2026, 10, 8)), (date(2026, 10, 9), date(2026, 10, 9))]
    outcome = await plan_from_slots(
        slots, catalogue=_catalogue(), windows=windows, as_of=TODAY,
        headline_metric_ids=["spend", "ret", "not_in_catalogue"],
    )
    assert outcome.plan is not None, outcome.stats
    assert outcome.plan.steps[0].metric_ids == ["spend"]  # ranked by the first headline measure


def test_a_sentence_that_only_lists_artifact_ids_is_dropped() -> None:
    from seleric_swarm.agent.validation import _strip_artifact_ids

    deps = _deps()
    ev = deps.artifact_store.put(_artifact("evidence", EvidenceArtifact(
        metric_id="spend", dimensions={}, grain="none", as_of=TODAY, period_start=TODAY,
        period_end=TODAY, value=1.0, source_query={},
    ).model_dump(mode="json")))
    text = (f"Spend fell sharply today against the same hours yesterday (see {ev.id}). "
            f'Evidence IDs: ["{ev.id}"].\n\nPeriod: today')
    out = _strip_artifact_ids(MissionResult(status="completed", final_response=text), deps).final_response
    assert ev.id not in out and "Evidence IDs" not in out
    assert out.startswith("Spend fell sharply today against the same hours yesterday") and "Period: today" in out


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
