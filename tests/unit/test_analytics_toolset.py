"""Unit + regression tests for toolsets/analytics.py (Sprint 1, Profile C).

The headline regression is ``docs/BUG_SHEET.md`` #14, ported forward in
**both** halves per ``03_PROFILE_CAPABILITIES.md`` §9 exit criterion (a)2:

1. per-day evidence reaches the detector un-normalized, and
2. a grain-mismatched evidence set is *rejected*, not normalized.

Half 2 is the new one. In swarm_v2 the guarantee came from a typed
``granularity`` threaded through mission state so the fetcher and the
detector could not disagree. V3's rules 4+5 delete that thread — the LLM
picks the grain per call — so the guarantee has to live in the tool as a
precondition, or #14 returns as an intermittent instead of a test failure.

Fake-context pattern mirrors ``tests/unit/test_semantic_toolset.py`` (Profile
B) so the two toolsets' tests stay readable side by side.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.conversations.contracts import (
    Artifact,
    ArtifactProvenance,
    ContextBundle,
    Principal,
)
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import analytics


class FakeRunContext:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _deps(store: InMemoryArtifactStore | None = None) -> SelericDeps:
    return SelericDeps(
        mission_id="mission-1",
        as_of=datetime(2026, 9, 18, tzinfo=UTC),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"),
        thread_id="thread-1",
        run_id="run-1",
        trace_id="trace-1",
        context=ContextBundle(),
        mcp_client=object(),  # analytics tools never touch MCP — rule 5
        artifact_store=store or InMemoryArtifactStore(),
        limits=ExecutionLimits(),
    )


def _put_evidence(
    store: InMemoryArtifactStore,
    *,
    metric: str = "metric.net_sales",
    grain: str = "day",
    start: str,
    end: str,
    value: float | None,
    dimensions: dict[str, str] | None = None,
) -> str:
    evidence = EvidenceArtifact(
        metric_id=metric,
        dimensions=dimensions or {},
        grain=grain,  # type: ignore[arg-type]
        as_of=datetime(2026, 9, 18, tzinfo=UTC),
        period_start=datetime.fromisoformat(start).replace(tzinfo=UTC),
        period_end=datetime.fromisoformat(end).replace(tzinfo=UTC),
        value=value,
        source_query={"measure": metric},
    )
    artifact = store.put(
        Artifact(
            workspace_id="ws1",
            artifact_type="evidence",
            payload=evidence.model_dump(mode="json"),
            classification="factual",
            evidence_ids=[f"raw:{metric}:{start}:{end}"],
            provenance=ArtifactProvenance(query_version="q1"),
            mission_id="mission-1",
        )
    )
    return artifact.id


def _daily(store: InMemoryArtifactStore, values: dict[str, float], **kw: Any) -> list[str]:
    return [_put_evidence(store, start=day, end=day, value=value, **kw) for day, value in values.items()]


def _findings(store: InMemoryArtifactStore, result: Any) -> list[dict[str, Any]]:
    return [store.get(aid).payload for aid in result.artifact_ids]


# ---- docs/BUG_SHEET.md #14, half 1: per-day evidence is not normalized -------------


@pytest.mark.asyncio
async def test_per_day_evidence_reaches_detector_unnormalized():
    """Five real daily rows must be scored as five daily values. The old
    pipeline's failure mode was a 5-day SUM reaching a single-day baseline;
    the band-aid then divided it by 5. Neither happens here -- the observed
    value carried into the Finding is the raw day's figure."""
    store = InMemoryArtifactStore()
    ids = _daily(
        store,
        {
            "2026-09-12": 4100.0,
            "2026-09-13": 4050.0,
            "2026-09-14": 4120.0,
            "2026-09-15": 4080.0,
            "2026-09-16": 900.0,  # the drop
        },
    )
    ctx = FakeRunContext(_deps(store))

    result = await analytics.detect_anomalies(ctx, ids)

    assert result.success, result.summary
    payloads = _findings(store, result)
    assert len(payloads) == 1
    metrics = payloads[0]["metrics"]
    assert metrics["observed"] == 900.0, "observed must be the raw daily value, not a normalized aggregate"
    assert metrics["expected"] == pytest.approx(4090.0), "baseline is the median of the 4 prior days"
    assert payloads[0]["finding_type"] == "anomaly"
    assert len(payloads[0]["evidence_ids"]) == 5


# ---- docs/BUG_SHEET.md #14, half 2: a mismatched set is refused, never normalized ---


@pytest.mark.asyncio
async def test_multi_day_aggregate_labelled_day_grain_is_rejected():
    """The #14 artifact shape: one row covering 2026-09-12..16 (a 5-day sum)
    carrying grain="day". swarm_v2 divided it by 5 and compared the result
    to a single-day band. Here it is refused outright."""
    store = InMemoryArtifactStore()
    aggregate = _put_evidence(store, grain="day", start="2026-09-12", end="2026-09-16", value=251328.21)
    ctx = FakeRunContext(_deps(store))

    result = await analytics.detect_anomalies(ctx, [aggregate])

    assert result.success is False
    assert result.error_code == "EVIDENCE_GRAIN_MISMATCH"
    assert result.artifact_ids == []
    assert "spans 5 day(s)" in result.summary


@pytest.mark.asyncio
async def test_aggregate_mixed_into_a_daily_series_is_rejected():
    """The subtler version: four genuine daily rows plus one window
    aggregate. Pooling them would score a 5-day sum against a daily median
    -- exactly the +208% false spike from the original trace."""
    store = InMemoryArtifactStore()
    ids = _daily(store, {"2026-09-12": 4100.0, "2026-09-13": 4050.0, "2026-09-14": 4120.0})
    ids.append(_put_evidence(store, grain="day", start="2026-09-15", end="2026-09-19", value=251328.21))
    ctx = FakeRunContext(_deps(store))

    result = await analytics.detect_anomalies(ctx, ids)

    assert result.success is False
    assert result.error_code == "EVIDENCE_GRAIN_MISMATCH"


@pytest.mark.asyncio
async def test_mixed_grain_set_is_rejected():
    store = InMemoryArtifactStore()
    ids = [
        _put_evidence(store, grain="day", start="2026-09-15", end="2026-09-15", value=4100.0),
        _put_evidence(store, grain="month", start="2026-08-01", end="2026-08-31", value=120000.0),
    ]
    ctx = FakeRunContext(_deps(store))

    result = await analytics.detect_anomalies(ctx, ids)

    assert result.success is False
    assert result.error_code == "EVIDENCE_GRAIN_MISMATCH"
    assert "mixes grains" in result.summary


# ---- detect_anomalies: remaining behavior ------------------------------------------


@pytest.mark.asyncio
async def test_stable_series_produces_no_findings_but_still_succeeds():
    """No anomaly is a valid answer, not a failure. Returning success=False
    here would push the agent to retry a query that was already correct."""
    store = InMemoryArtifactStore()
    ids = _daily(
        store,
        {"2026-09-12": 100.0, "2026-09-13": 101.0, "2026-09-14": 99.0, "2026-09-15": 100.0, "2026-09-16": 100.5},
    )
    ctx = FakeRunContext(_deps(store))

    result = await analytics.detect_anomalies(ctx, ids)

    assert result.success is True
    assert result.artifact_ids == []


@pytest.mark.asyncio
async def test_single_point_series_refuses_rather_than_inventing_a_baseline():
    """One observation has no history to score against. swarm_v2 expressed
    this as a policy() gate that skipped the specialist (docs/BUG_SHEET.md #8
    records those gates working); here it is a precondition refusal
    (CONTRACTS.md A1.3)."""
    store = InMemoryArtifactStore()
    ids = _daily(store, {"2026-09-16": 4100.0})
    ctx = FakeRunContext(_deps(store))

    result = await analytics.detect_anomalies(ctx, ids)

    assert result.success is False
    assert result.error_code == "INSUFFICIENT_EVIDENCE"
    assert "need >=2" in result.summary


@pytest.mark.asyncio
async def test_series_are_scored_independently_per_metric():
    """Two metrics in one call must not pool their histories."""
    store = InMemoryArtifactStore()
    ids = _daily(store, {"2026-09-12": 100.0, "2026-09-13": 100.0, "2026-09-14": 100.0, "2026-09-15": 700.0})
    ids += _daily(
        store,
        {"2026-09-12": 50.0, "2026-09-13": 50.0, "2026-09-14": 50.0, "2026-09-15": 50.0},
        metric="metric.spend",
    )
    ctx = FakeRunContext(_deps(store))

    result = await analytics.detect_anomalies(ctx, ids)

    assert result.success is True
    payloads = _findings(store, result)
    assert len(payloads) == 1, "only net_sales moved; spend is flat and must not be flagged"
    assert "metric.net_sales" in payloads[0]["statement"]


@pytest.mark.asyncio
async def test_unimplemented_method_refuses_instead_of_silently_substituting():
    """"seasonal" is in the frozen signature but has no implementation in
    src/. Running robust_zscore instead would answer a different question
    than the agent asked."""
    store = InMemoryArtifactStore()
    ids = _daily(store, {"2026-09-15": 1.0, "2026-09-16": 2.0})
    ctx = FakeRunContext(_deps(store))

    result = await analytics.detect_anomalies(ctx, ids, method="seasonal")

    assert result.success is False
    assert result.error_code == "METHOD_NOT_AVAILABLE"


@pytest.mark.asyncio
async def test_mad_is_an_alias_for_robust_zscore_not_a_second_implementation():
    store = InMemoryArtifactStore()
    ids = _daily(store, {"2026-09-13": 100.0, "2026-09-14": 100.0, "2026-09-15": 100.0, "2026-09-16": 900.0})
    ctx = FakeRunContext(_deps(store))

    assert (await analytics.detect_anomalies(ctx, ids, method="mad")).success is True


# ---- compare_periods -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_delta_is_period_a_minus_period_b_so_a_decline_reads_negative():
    """Sign convention carried from observer.py::_post_comparison_deltas and
    services/intelligence/observer.py::_comparison_deltas. Flipping it would
    silently invert the direction of every comparison finding."""
    store = InMemoryArtifactStore()
    period_a = _put_evidence(store, grain="month", start="2026-08-01", end="2026-08-31", value=90000.0)
    period_b = _put_evidence(store, grain="month", start="2026-07-01", end="2026-07-31", value=120000.0)
    ctx = FakeRunContext(_deps(store))

    result = await analytics.compare_periods(ctx, [period_a, period_b])

    assert result.success is True
    metrics = _findings(store, result)[0]["metrics"]
    assert metrics["delta"] == pytest.approx(-30000.0)
    assert metrics["delta_pct"] == pytest.approx(-25.0)


@pytest.mark.asyncio
async def test_metric_present_in_only_one_period_is_skipped_not_zero_filled():
    """An absent measurement is not a measurement of zero -- the same
    never-fabricate discipline docs/BUG_SHEET.md #7 turns on."""
    store = InMemoryArtifactStore()
    ids = [
        _put_evidence(store, grain="month", start="2026-08-01", end="2026-08-31", value=90000.0),
        _put_evidence(store, metric="metric.spend", grain="month", start="2026-08-01", end="2026-08-31", value=500.0),
        _put_evidence(store, grain="month", start="2026-07-01", end="2026-07-31", value=120000.0),
    ]
    ctx = FakeRunContext(_deps(store))

    result = await analytics.compare_periods(ctx, ids)

    assert result.success is True
    payloads = _findings(store, result)
    assert len(payloads) == 1
    assert "metric.net_sales" in payloads[0]["statement"]


@pytest.mark.asyncio
async def test_compare_periods_needs_exactly_two_periods():
    store = InMemoryArtifactStore()
    ids = _daily(store, {"2026-09-12": 1.0, "2026-09-13": 2.0, "2026-09-14": 3.0})
    ctx = FakeRunContext(_deps(store))

    result = await analytics.compare_periods(ctx, ids)

    assert result.success is False
    assert result.error_code == "INSUFFICIENT_EVIDENCE"
    assert "exactly 2 distinct periods" in result.summary


# ---- evidence loading / provenance ---------------------------------------------------


@pytest.mark.asyncio
async def test_missing_evidence_id_is_reported_not_silently_skipped():
    """Computing over a subset of what the agent asked for is how a finding
    ends up citing evidence that didn't back it (rule 6)."""
    store = InMemoryArtifactStore()
    ids = _daily(store, {"2026-09-15": 1.0, "2026-09-16": 2.0})
    ctx = FakeRunContext(_deps(store))

    result = await analytics.detect_anomalies(ctx, [*ids, "artifact-does-not-exist"])

    assert result.success is False
    assert result.error_code == "INSUFFICIENT_EVIDENCE"
    assert "artifact-does-not-exist" in result.summary


@pytest.mark.asyncio
async def test_empty_evidence_ids_refuses():
    result = await analytics.detect_anomalies(FakeRunContext(_deps()), [])
    assert result.success is False
    assert result.error_code == "INSUFFICIENT_EVIDENCE"


@pytest.mark.asyncio
async def test_finding_artifacts_carry_evidence_and_calculation_version():
    """conversations/contracts.py::Artifact.require_provenance() rejects a
    derived artifact with no calculation version -- rule 6 enforced at write
    time, not by convention."""
    store = InMemoryArtifactStore()
    ids = _daily(store, {"2026-09-13": 100.0, "2026-09-14": 100.0, "2026-09-15": 100.0, "2026-09-16": 900.0})
    ctx = FakeRunContext(_deps(store))

    result = await analytics.detect_anomalies(ctx, ids)

    artifact = store.get(result.artifact_ids[0])
    assert artifact.classification == "derived"
    assert artifact.provenance.calculation_version == "analytics.v1"
    assert artifact.provenance.evidence_ids == ids
    assert artifact.mission_id == "mission-1"


@pytest.mark.asyncio
async def test_a_finding_is_not_valid_input_evidence():
    """Findings are derived; feeding one back in as evidence would let a
    calculation be scored as if it were a measurement."""
    store = InMemoryArtifactStore()
    ids = _daily(store, {"2026-09-13": 100.0, "2026-09-14": 100.0, "2026-09-15": 100.0, "2026-09-16": 900.0})
    ctx = FakeRunContext(_deps(store))
    finding_id = (await analytics.detect_anomalies(ctx, ids)).artifact_ids[0]

    result = await analytics.detect_anomalies(ctx, [finding_id])

    assert result.success is False
    assert result.error_code == "INSUFFICIENT_EVIDENCE"
    assert "not evidence" in result.summary
