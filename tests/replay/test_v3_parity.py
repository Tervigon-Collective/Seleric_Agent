"""Behavioral parity: swarm_v2 specialists vs. V3 toolsets (Profile C, Sprint 3).

``03_PROFILE_CAPABILITIES.md`` §9 criterion (d)7. Before this, Profile C — the
profile the plan itself calls highest-behavioral-risk — had no criterion asking
whether the new code reaches the *same conclusion*; it had regression tests for
known bugs plus a schema-completeness check a required field satisfies for free.

Both paths are fed the **same evidence** and driven directly, not through an LLM
loop, so a divergence is attributable to the capability rather than to tool
selection (Sprint 4's eval gate covers the latter). Structural equivalence is
the bar: the set of ``(metric, direction)`` anomalies, the evidence
classifications, the hypothesis statements. Floats are reported, not asserted —
see ``evals/parity.py`` for why exact numeric parity would fail on changes that
are intentional.

Offline: real `AnomalyAgent` against a real `Blackboard` on one side, the real
`detect_anomalies` tool on the other. No `runtime` fixture, no MCP, no LLM.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.conversations.contracts import (
    Artifact,
    ArtifactProvenance,
    ContextBundle,
    Principal,
    PrincipalAuthMethod,
)
from seleric_swarm.evals.parity import (
    AnomalySignature,
    anomalies_from_blackboard,
    anomalies_from_findings,
    compare,
)
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.swarm.artifacts import Evidence
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.mission import SwarmMission
from seleric_swarm.swarm.providers.base import AnomalyFinding, MetricReading
from seleric_swarm.swarm.specialists.anomaly import AnomalyAgent
from seleric_swarm.toolsets import analytics

_METRIC = "metric.net_sales"
_MISSION = "MS-parity"
# A clean daily series with one obvious drop on the last day. Both paths get
# exactly these numbers.
_SERIES = [4100.0, 4050.0, 4120.0, 4080.0, 4110.0, 4090.0, 4105.0, 900.0]


class _FakeProviders:
    def __init__(self, detector: Any) -> None:
        self.anomaly = detector


class _BandDetector:
    """Stand-in for swarm_v2's detector, scoring each reading against a fixed
    band computed from the same history the V3 path uses.

    It is not RobustZScoreDetector itself: that class calls
    BusinessStateService.get_metric_state() to fetch its own history, which
    needs a live runtime. Holding the band constant across both paths is what
    isolates the comparison to the *decision* — flagged or not, and which
    direction — rather than to where the baseline came from.
    """

    def __init__(self, history: list[float]) -> None:
        ordered = sorted(history)
        mid = ordered[len(ordered) // 2]
        self.band = [mid * 0.8, mid * 1.2]
        self.midpoint = mid

    async def detect(
        self, readings: list[MetricReading], *, context: dict[str, Any]
    ) -> list[AnomalyFinding]:
        out: list[AnomalyFinding] = []
        for reading in readings:
            if reading.value is None or self.band[0] <= reading.value <= self.band[1]:
                continue
            direction = "up" if reading.value > self.midpoint else "down"
            out.append(
                AnomalyFinding(
                    metric_id=reading.metric_id,
                    observed=reading.value,
                    expected_range=list(self.band),
                    deviation_pct=(reading.value - self.midpoint) / self.midpoint * 100,
                    score=3.5,
                    direction=direction,
                    direction_bad="down",
                    adverse=direction == "down",
                    magnitude_score=3.5,
                    adversity_score=3.5 if direction == "down" else 0.0,
                    detector={"id": "parity_band", "z_threshold": 3.0},
                    dimensions=reading.dimensions,
                    data_origin="FIXTURE",
                    synthetic=False,
                )
            )
        return out


def _day(index: int) -> datetime:
    return datetime(2026, 9, 10, tzinfo=UTC) + timedelta(days=index)


async def _legacy_anomalies() -> set[AnomalySignature]:
    """swarm_v2: ObserverAgent's per-day Evidence -> AnomalyAgent -> Blackboard."""
    blackboard = Blackboard(_MISSION)
    for index, value in enumerate(_SERIES):
        blackboard.post(
            Evidence.new(
                mission_id=_MISSION,
                created_by="observer_agent",
                metric_or_fact=_METRIC,
                value=value,
                time_range={"start": _day(index).date().isoformat(), "end": _day(index).date().isoformat()},
            )
        )
    agent = AnomalyAgent(_FakeProviders(_BandDetector(_SERIES[:-1])))
    mission = SwarmMission(
        mission_id=_MISSION,
        query="why did net sales drop",
        time_range={"start": _day(0).date().isoformat(), "end": _day(7).date().isoformat()},
    )
    await agent.run(blackboard, mission)
    return anomalies_from_blackboard(blackboard.by_type("anomaly"))


async def _v3_anomalies() -> tuple[set[AnomalySignature], list[str]]:
    """V3: the same numbers as EvidenceArtifacts -> detect_anomalies -> Findings."""
    store = InMemoryArtifactStore()
    deps = SelericDeps(
        mission_id=_MISSION,
        as_of=datetime(2026, 9, 20, tzinfo=UTC),
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

    ids: list[str] = []
    for index, value in enumerate(_SERIES):
        payload = EvidenceArtifact(
            metric_id=_METRIC,
            grain="day",
            as_of=deps.as_of,
            period_start=_day(index),
            period_end=_day(index),
            value=value,
            source_query={"measure": _METRIC},
        )
        ids.append(
            store.put(
                Artifact(
                    workspace_id="ws-1",
                    artifact_type="evidence",
                    payload=payload.model_dump(mode="json"),
                    classification="factual",
                    evidence_ids=[f"raw:{_METRIC}:{index}"],
                    provenance=ArtifactProvenance(query_version="q1"),
                    mission_id=_MISSION,
                )
            ).id
        )

    class _Ctx:
        deps = None

    ctx = _Ctx()
    ctx.deps = deps  # type: ignore[assignment]
    result = await analytics.detect_anomalies(ctx, ids)
    assert result.success, result.summary

    findings = [store.get(aid).payload for aid in result.artifact_ids]
    numeric = [
        f"z={f['metrics'].get('z_score'):.2f} observed={f['metrics'].get('observed')}"
        for f in findings
    ]
    return anomalies_from_findings(findings), numeric


@pytest.mark.asyncio
async def test_anomaly_detection_reaches_the_same_conclusion_on_the_same_evidence():
    """The headline parity case: given one identical daily series, both paths
    must flag the same metric moving in the same direction.

    The z-score and the band differ between them — reported below, not
    asserted — because V3 derives its baseline from the evidence while swarm_v2
    scores against a detector-supplied band. What must not differ is the
    conclusion.
    """
    legacy = await _legacy_anomalies()
    v3, numeric = await _v3_anomalies()

    report = compare(
        "net_sales daily drop",
        legacy_anomalies=legacy,
        v3_anomalies=v3,
        numeric=numeric,
    )

    assert report.passed, "\n" + report.render()
    assert legacy, "the legacy path must actually flag something, or this proves nothing"
    assert {a.direction for a in v3} == {"down"}


@pytest.mark.asyncio
async def test_parity_report_flags_an_unexplained_divergence():
    """The harness has to be able to fail, or it is not a gate.

    Without this, a bug that made both paths return nothing would read as
    perfect parity.
    """
    report = compare(
        "synthetic divergence",
        legacy_anomalies={AnomalySignature(metric_id=_METRIC, direction="down")},
        v3_anomalies=set(),
    )

    assert not report.passed
    assert len(report.unexplained) == 1
    assert "FAIL" in report.render()


@pytest.mark.asyncio
async def test_a_registered_intentional_divergence_is_explained_not_failed():
    """docs/BUG_SHEET.md #14's fix *is* a behavior change: where swarm_v2
    normalized a multi-day sum and flagged it, V3 refuses the set outright. The
    report must distinguish that from an unexplained regression."""
    signature = AnomalySignature(metric_id=_METRIC, direction="up")
    report = compare(
        "multi-day aggregate",
        legacy_anomalies={signature},
        v3_anomalies=set(),
        expected={signature: "grain_refusal"},
    )

    assert report.passed
    assert report.divergences and report.divergences[0].explained
    assert "EVIDENCE_GRAIN_MISMATCH" in report.render()
