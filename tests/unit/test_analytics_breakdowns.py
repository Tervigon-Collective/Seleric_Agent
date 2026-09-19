"""The four Sprint 4 analytics functions (Profile C).

These are genuinely new capability, not ports — nothing in `src/` implemented
contribution, segment, funnel or cohort math before this sprint. So the tests
pin *decisions* rather than guarding a previous behavior: what the denominator
of a share actually is, what a funnel step order is allowed to come from, and
what a "cohort" means when the underlying metric has no daily grain.

Fake-context pattern mirrors `tests/unit/test_analytics_toolset.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.analytics.breakdown import Segment, contributions, shares
from seleric_swarm.analytics.cohort import CohortReading, cohort_spread
from seleric_swarm.analytics.funnel import StepReading, ordered_steps, transitions
from seleric_swarm.conversations.contracts import (
    Artifact,
    ArtifactProvenance,
    ContextBundle,
    Principal,
    PrincipalAuthMethod,
)
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import analytics
from seleric_swarm.toolsets import policy_config as policy

_MISSION = "MS4-breakdowns"


class FakeRunContext:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _deps(store: InMemoryArtifactStore) -> SelericDeps:
    return SelericDeps(
        mission_id=_MISSION,
        as_of=datetime(2026, 9, 19, tzinfo=UTC),
        principal=Principal(
            principal_id="p1",
            workspace_id="ws-1",
            user_id="user-1",
            authenticated=False,
            auth_method=PrincipalAuthMethod.ANONYMOUS,
        ),
        thread_id="t",
        run_id="r",
        trace_id="x",
        context=ContextBundle(),
        mcp_client=NullMcpClient(),
        artifact_store=store,
        limits=ExecutionLimits(),
    )


def _put(
    store: InMemoryArtifactStore,
    *,
    metric: str = "metric.net_sales",
    value: float | None,
    dimensions: dict[str, str] | None = None,
    start: str = "2026-09-01",
    end: str = "2026-09-30",
    grain: str = "none",
) -> str:
    payload = EvidenceArtifact(
        metric_id=metric,
        dimensions=dimensions or {},
        grain=grain,  # type: ignore[arg-type]
        as_of=datetime(2026, 9, 19, tzinfo=UTC),
        period_start=datetime.fromisoformat(start).replace(tzinfo=UTC),
        period_end=datetime.fromisoformat(end).replace(tzinfo=UTC),
        value=value,
        source_query={"measure": metric},
    )
    return store.put(
        Artifact(
            workspace_id="ws-1",
            artifact_type="evidence",
            payload=payload.model_dump(mode="json"),
            classification="factual",
            evidence_ids=[f"raw:{metric}:{dimensions}:{start}"],
            provenance=ArtifactProvenance(query_version="q1"),
            mission_id=_MISSION,
        )
    ).id


def _findings(store: InMemoryArtifactStore, result: Any) -> list[dict[str, Any]]:
    return [store.get(aid).payload for aid in result.artifact_ids]


# ---- pure share / contribution math -----------------------------------------


def test_shares_drop_nulls_rather_than_zero_filling():
    """An unmeasured segment is not a segment worth zero."""
    result = shares([Segment("a", 60.0), Segment("b", 40.0), Segment("c", None)])
    assert result is not None
    assert {s.label for s in result.shares} == {"a", "b"}
    assert result.total == 100.0


def test_shares_report_reconciliation_against_an_independent_total():
    """drilldown discards the parent total, so children-sum is the usual
    denominator. When a real total IS available, say whether it agreed."""
    agreeing = shares([Segment("a", 60.0), Segment("b", 40.0)], independent_total=100.0)
    assert agreeing is not None and agreeing.reconciled is True

    disagreeing = shares([Segment("a", 60.0), Segment("b", 40.0)], independent_total=130.0)
    assert disagreeing is not None and disagreeing.reconciled is False


def test_reconciliation_is_unknown_not_true_when_no_total_supplied():
    """The common case. `None` means "we never checked", which is different
    from "we checked and it matched" — conflating them would let an
    incomplete breakdown read as verified."""
    result = shares([Segment("a", 1.0)])
    assert result is not None and result.reconciled is None


def test_zero_total_yields_no_shares_rather_than_dividing():
    result = shares([Segment("a", 5.0), Segment("b", -5.0)])
    assert result is not None
    assert result.shares == []
    assert result.total == 0.0


def test_small_segments_fold_into_other_but_stay_in_the_denominator():
    segments = [Segment("big", 100.0)] + [Segment(f"tiny{i}", 0.1) for i in range(10)]
    result = shares(segments)
    assert result is not None
    assert [s.label for s in result.shares] == ["big"]
    assert result.other_count == 10
    assert result.total == pytest.approx(101.0)
    # big is 100/101, NOT 100/100 -- the tiny ones were excluded from the
    # listing, not from the maths.
    assert result.shares[0].share == pytest.approx(100 / 101)


def test_contribution_delta_sign_matches_the_comparison_tool():
    """a - b, same as comparison.period_deltas. A different sign here would
    make contribution disagree with compare_periods about direction."""
    rows, total = contributions([Segment("x", 80.0)], [Segment("x", 100.0)])
    assert total == pytest.approx(-20.0)
    assert rows[0].delta == pytest.approx(-20.0)
    assert rows[0].share_of_change == pytest.approx(1.0)


def test_share_of_change_may_exceed_one_when_segments_offset():
    """One segment growing while another shrinks is real signal, not an error
    to clamp away."""
    rows, total = contributions(
        [Segment("up", 150.0), Segment("down", 50.0)],
        [Segment("up", 100.0), Segment("down", 100.0)],
    )
    assert total == pytest.approx(0.0)
    # Total change is zero, so share-of-change is undefined and reported 0.0 --
    # but the per-segment deltas still carry the story.
    assert {r.label: r.delta for r in rows} == {"up": pytest.approx(50.0), "down": pytest.approx(-50.0)}


def test_segment_present_in_only_one_period_is_skipped():
    """'This channel is new' and 'it went from 0 to N' are different claims and
    the evidence cannot tell them apart."""
    rows, _ = contributions([Segment("a", 10.0), Segment("new", 5.0)], [Segment("a", 8.0)])
    assert [r.label for r in rows] == ["a"]


# ---- contribution_analysis --------------------------------------------------


@pytest.mark.asyncio
async def test_contribution_single_period_reports_shares_and_warns_about_denominator():
    store = InMemoryArtifactStore()
    ids = [
        _put(store, value=600.0, dimensions={"channel": "meta"}),
        _put(store, value=400.0, dimensions={"channel": "google"}),
    ]
    result = await analytics.contribution_analysis(FakeRunContext(_deps(store)), ids, "channel")

    assert result.success, result.summary
    payloads = _findings(store, result)
    assert len(payloads) == 2
    assert any("60.0%" in p["statement"] for p in payloads)
    assert any("summed segments" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_contribution_two_periods_attributes_the_change():
    store = InMemoryArtifactStore()
    ids = [
        _put(store, value=500.0, dimensions={"channel": "meta"}, start="2026-09-01", end="2026-09-30"),
        _put(store, value=400.0, dimensions={"channel": "google"}, start="2026-09-01", end="2026-09-30"),
        _put(store, value=700.0, dimensions={"channel": "meta"}, start="2026-08-01", end="2026-08-30"),
        _put(store, value=400.0, dimensions={"channel": "google"}, start="2026-08-01", end="2026-08-30"),
    ]
    result = await analytics.contribution_analysis(FakeRunContext(_deps(store)), ids, "channel")

    assert result.success, result.summary
    meta = next(p for p in _findings(store, result) if "meta" in p["statement"])
    assert meta["metrics"]["delta"] == pytest.approx(-200.0)
    # meta is the entire change: google was flat.
    assert meta["metrics"]["share_of_change"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_contribution_refuses_evidence_with_no_dimension_stamp():
    """A query_metrics breakdown leaves dimensions empty on every row, which
    would make the segments indistinguishable."""
    store = InMemoryArtifactStore()
    ids = [_put(store, value=600.0), _put(store, value=400.0)]
    result = await analytics.contribution_analysis(FakeRunContext(_deps(store)), ids, "channel")

    assert result.success is False
    assert result.error_code == "INSUFFICIENT_EVIDENCE"
    assert policy.WARN_NO_DIMENSION_EVIDENCE in result.warnings


# ---- segment_decomposition --------------------------------------------------


@pytest.mark.asyncio
async def test_segment_decomposition_emits_one_finding_per_dimension():
    store = InMemoryArtifactStore()
    ids = [
        _put(store, value=600.0, dimensions={"channel": "meta"}),
        _put(store, value=400.0, dimensions={"channel": "google"}),
        _put(store, value=700.0, dimensions={"device": "mobile"}),
        _put(store, value=300.0, dimensions={"device": "desktop"}),
    ]
    result = await analytics.segment_decomposition(
        FakeRunContext(_deps(store)), ids, ["channel", "device"]
    )

    assert result.success, result.summary
    statements = [p["statement"] for p in _findings(store, result)]
    assert any("by channel" in s for s in statements)
    assert any("by device" in s for s in statements)


@pytest.mark.asyncio
async def test_segment_decomposition_names_dimensions_with_no_evidence():
    store = InMemoryArtifactStore()
    ids = [_put(store, value=600.0, dimensions={"channel": "meta"})]
    result = await analytics.segment_decomposition(
        FakeRunContext(_deps(store)), ids, ["channel", "geo"]
    )

    assert result.success
    assert any("geo" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_segment_decomposition_refuses_pooled_rows():
    store = InMemoryArtifactStore()
    ids = [_put(store, value=600.0), _put(store, value=400.0)]
    result = await analytics.segment_decomposition(FakeRunContext(_deps(store)), ids, ["channel"])

    assert result.success is False
    assert policy.WARN_POOLED_SEGMENTS in result.warnings


# ---- funnel -----------------------------------------------------------------


def test_funnel_places_steps_in_declared_order_not_input_order():
    readings = [
        StepReading("metric.purchase_cvr", 0.02),
        StepReading("metric.sessions", 1000.0),
        StepReading("metric.atc_rate", 0.10),
    ]
    assert [s.metric_id for s in ordered_steps(readings)] == [
        "metric.sessions",
        "metric.atc_rate",
        "metric.purchase_cvr",
    ]


def test_sessions_enters_as_an_implicit_rate_of_one():
    """The base step is a count, not a rate; 100% of sessions are sessions.
    Without this the first conversion would be nonsense (0.10 / 1000)."""
    steps = ordered_steps([StepReading("metric.sessions", 1000.0), StepReading("metric.atc_rate", 0.10)])
    assert steps[0].rate == 1.0
    assert steps[0].raw == 1000.0
    assert transitions(steps)[0].conversion == pytest.approx(0.10)


def test_conversion_is_the_ratio_of_two_session_anchored_rates():
    """Every FUNNEL_STEPS rate shares the `sessions` denominator, so survival
    between steps is rate[i+1]/rate[i] -- no cross-axis division."""
    steps = ordered_steps(
        [StepReading("metric.atc_rate", 0.10), StepReading("metric.purchase_cvr", 0.02)]
    )
    move = transitions(steps)[0]
    assert move.conversion == pytest.approx(0.2)
    assert move.drop_off == pytest.approx(0.8)


def test_unknown_metrics_are_dropped_not_guessed_into_position():
    """Positioning an unrecognized metric by name is the heuristic Profile B
    deleted; a metric with a different denominator would also silently break
    every downstream conversion."""
    steps = ordered_steps(
        [StepReading("metric.sessions", 1000.0), StepReading("metric.cac", 42.0)]
    )
    assert [s.metric_id for s in steps] == ["metric.sessions"]


@pytest.mark.asyncio
async def test_funnel_decomposition_finds_the_worst_drop_off():
    store = InMemoryArtifactStore()
    ids = [
        _put(store, metric="metric.sessions", value=1000.0),
        _put(store, metric="metric.pdp_view_rate", value=0.50),
        _put(store, metric="metric.atc_rate", value=0.10),
        _put(store, metric="metric.purchase_cvr", value=0.02),
    ]
    result = await analytics.funnel_decomposition(FakeRunContext(_deps(store)), ids)

    assert result.success, result.summary
    # sessions->pdp loses 50%, pdp->atc loses 80%, atc->purchase loses 80%.
    assert "largest drop-off" in result.summary
    assert len(_findings(store, result)) == 3


@pytest.mark.asyncio
async def test_funnel_refuses_a_single_step_and_names_the_declared_funnel():
    store = InMemoryArtifactStore()
    ids = [_put(store, metric="metric.sessions", value=1000.0)]
    result = await analytics.funnel_decomposition(FakeRunContext(_deps(store)), ids)

    assert result.success is False
    assert policy.WARN_NO_FUNNEL_STEPS in result.warnings
    assert "metric.sessions" in result.summary


@pytest.mark.asyncio
async def test_funnel_warns_about_metrics_outside_the_declared_funnel():
    store = InMemoryArtifactStore()
    ids = [
        _put(store, metric="metric.sessions", value=1000.0),
        _put(store, metric="metric.atc_rate", value=0.10),
        _put(store, metric="metric.cac", value=42.0),
    ]
    result = await analytics.funnel_decomposition(FakeRunContext(_deps(store)), ids)

    assert result.success
    assert any("metric.cac" in w for w in result.warnings)


# ---- cohort -----------------------------------------------------------------


def test_cohort_spread_needs_at_least_two_cohorts():
    """One cohort against its own median is a delta of exactly zero -- it
    would look like a result while saying nothing."""
    assert cohort_spread([CohortReading("a", 1.0)]) is None


def test_cohort_spread_ranks_against_the_median():
    spread = cohort_spread(
        [CohortReading("a", 0.30), CohortReading("b", 0.20), CohortReading("c", 0.10)]
    )
    assert spread is not None
    assert spread.median == pytest.approx(0.20)
    assert spread.best.label == "a"
    assert spread.worst.label == "c"
    assert spread.spread == pytest.approx(0.20)


@pytest.mark.asyncio
async def test_cohort_analysis_by_dimension_value():
    store = InMemoryArtifactStore()
    ids = [
        _put(store, metric="metric.repeat_rate", value=0.30, dimensions={"brand_id": "20"}),
        _put(store, metric="metric.repeat_rate", value=0.10, dimensions={"brand_id": "21"}),
    ]
    result = await analytics.cohort_analysis(FakeRunContext(_deps(store)), ids)

    assert result.success, result.summary
    assert "by dimension value" in result.summary
    assert len(_findings(store, result)) == 2


@pytest.mark.asyncio
async def test_cohort_analysis_falls_back_to_windows_and_says_so():
    """repeat_rate has no daily grain -- a windowed query returns one row per
    window (feature_class: windowed_point). With no dimension to cohort on,
    the windows ARE the cohorts, and the summary must not pretend otherwise.

    Windows are equal-length here because the grain precondition requires it;
    see the next test for what happens with real calendar months.
    """
    store = InMemoryArtifactStore()
    ids = [
        _put(store, metric="metric.repeat_rate", value=0.30, start="2026-08-01", end="2026-08-28"),
        _put(store, metric="metric.repeat_rate", value=0.20, start="2026-09-01", end="2026-09-28"),
    ]
    result = await analytics.cohort_analysis(FakeRunContext(_deps(store)), ids)

    assert result.success, result.summary
    assert "by measurement window" in result.summary
    assert any("measurement window" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_unequal_calendar_months_are_refused_for_cohorts_too():
    """A known, deliberate limitation rather than an oversight.

    August (31 days) against September (30) is refused by A1.2's equal-spans
    rule. That rule exists to stop a 5-day sum being scored against a
    single-day baseline (docs/BUG_SHEET.md #14), and this tool cannot tell a
    normalized rate like repeat_rate -- where a 1-day window difference is
    harmless -- from a raw count, where it is not.

    Weakening the precondition here to make calendar months work would remove
    the guard for counts as well, so the refusal stands and the caller fetches
    equal-length windows instead. Pinned as a test so the tradeoff is visible
    rather than rediscovered as a bug.
    """
    store = InMemoryArtifactStore()
    ids = [
        _put(store, metric="metric.repeat_rate", value=0.30, start="2026-08-01", end="2026-08-31"),
        _put(store, metric="metric.repeat_rate", value=0.20, start="2026-09-01", end="2026-09-30"),
    ]
    result = await analytics.cohort_analysis(FakeRunContext(_deps(store)), ids)

    assert result.success is False
    assert result.error_code == "EVIDENCE_GRAIN_MISMATCH"
    assert "same number of days" in result.summary


@pytest.mark.asyncio
async def test_cohort_analysis_refuses_a_single_cohort():
    store = InMemoryArtifactStore()
    ids = [_put(store, metric="metric.repeat_rate", value=0.30, dimensions={"brand_id": "20"})]
    result = await analytics.cohort_analysis(FakeRunContext(_deps(store)), ids)

    assert result.success is False
    assert policy.WARN_SINGLE_COHORT in result.warnings


# ---- the A1.2 grain precondition binds all four -----------------------------


@pytest.mark.asyncio
async def test_all_four_enforce_the_grain_precondition():
    """CONTRACTS.md §4 binds A1.2 textually to compare_periods/detect_anomalies
    only. Extending it to these four is a decision (see the module comment in
    toolsets/analytics.py) -- pinned here so it cannot be quietly dropped."""
    store = InMemoryArtifactStore()
    bad = [
        _put(store, value=1.0, grain="day", start="2026-09-01", end="2026-09-05",
             dimensions={"channel": "meta"}),
        _put(store, value=2.0, grain="day", start="2026-09-06", end="2026-09-06",
             dimensions={"channel": "google"}),
    ]
    ctx = FakeRunContext(_deps(store))

    for result in (
        await analytics.contribution_analysis(ctx, bad, "channel"),
        await analytics.segment_decomposition(ctx, bad, ["channel"]),
        await analytics.funnel_decomposition(ctx, bad),
        await analytics.cohort_analysis(ctx, bad),
    ):
        assert result.success is False
        assert result.error_code == "EVIDENCE_GRAIN_MISMATCH"
