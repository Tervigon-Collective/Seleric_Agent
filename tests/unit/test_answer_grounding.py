"""Answer grounding: per-period coverage (REVISE) and ungrounded-number reporting.

The replay reproduces the failed "top products, October 2025 vs August 2026"
mission: both months were drilled down, the answer listed October and said
"No data available" for August. That must be REVISE, never PASS or REJECT.
"""

from __future__ import annotations

from datetime import UTC, datetime

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.agent.output import MissionResult
from seleric_swarm.agent.validation import EvidenceValidator
from seleric_swarm.agent.validation.grounding import scan_numbers
from seleric_swarm.conversations.contracts import (
    Artifact,
    ArtifactProvenance,
    ContextBundle,
    Principal,
)
from seleric_swarm.state.artifacts import InMemoryArtifactStore

MISSION = "MS3-grounding"
OCT = (datetime(2025, 10, 1, tzinfo=UTC), datetime(2025, 10, 31, tzinfo=UTC))
AUG = (datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 8, 31, tzinfo=UTC))


def _deps(store: InMemoryArtifactStore) -> SelericDeps:
    return SelericDeps(
        mission_id=MISSION,
        as_of=datetime(2026, 9, 28, tzinfo=UTC),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"),
        thread_id="t1",
        run_id="r1",
        trace_id="tr1",
        context=ContextBundle(),
        mcp_client=NullMcpClient(),
        artifact_store=store,
        limits=ExecutionLimits(),
    )


def _drilldown_rows(store: InMemoryArtifactStore, period, rows: dict[str, float]) -> list[str]:
    ids = []
    for label, value in rows.items():
        ev = EvidenceArtifact(
            metric_id="product_net_revenue",
            dimensions={"product_id": label.lower(), "product_title": label},
            grain="none",
            as_of=datetime(2026, 9, 28, tzinfo=UTC),
            period_start=period[0],
            period_end=period[1],
            value=value,
            source_query={"parent_query_id": "q"},
        )
        ids.append(
            store.put(
                Artifact(
                    workspace_id="ws1",
                    artifact_type="evidence",
                    payload=ev.model_dump(mode="json"),
                    classification="factual",
                    evidence_ids=[f"raw:{label}"],
                    provenance=ArtifactProvenance(query_version="q1"),
                    mission_id=MISSION,
                )
            ).id
        )
    return ids


def _result(text: str, evidence_ids: list[str]) -> MissionResult:
    return MissionResult(
        mission_id=MISSION,
        status="completed",
        query="compare top products sold in October 2025 and August 2026",
        as_of=datetime(2026, 9, 28, tzinfo=UTC),
        final_response=text,
        evidence_ids=evidence_ids,
    )


def _seed(store: InMemoryArtifactStore) -> list[str]:
    oct_ids = _drilldown_rows(store, OCT, {f"Alpha{i}": 50_000.0 + i * 1_000 for i in range(6)})
    aug_ids = _drilldown_rows(store, AUG, {f"Beta{i}": 40_000.0 + i * 1_000 for i in range(6)})
    return oct_ids + aug_ids


def test_replay_missing_period_is_revise_not_reject() -> None:
    store = InMemoryArtifactStore()
    ids = _seed(store)
    answer = (
        "Top products in October 2025: Alpha5 ₹55,000, Alpha4 ₹54,000, Alpha3 ₹53,000. "
        "August 2026: No data available."
    )
    outcome = EvidenceValidator().validate(_result(answer, ids), deps=_deps(store))
    assert outcome.verdict == "REVISE"
    assert "2026-08-01..2026-08-31" in (outcome.reason or "")


def test_both_periods_reported_passes_grounding() -> None:
    store = InMemoryArtifactStore()
    ids = _seed(store)
    answer = (
        "October 2025: Alpha5 ₹55,000, Alpha4 ₹54,000. "
        "August 2026: Beta5 ₹45,000, Beta4 ₹44,000."
    )
    outcome = EvidenceValidator().validate(_result(answer, ids), deps=_deps(store))
    assert "answer_grounding" not in (outcome.reason or "")
    assert "shows no value" not in (outcome.reason or "")


def test_scaled_display_values_count_as_grounded() -> None:
    store = InMemoryArtifactStore()
    ids = _seed(store)
    answer = "October 2025 leader: ₹0.55 lakh-scale, i.e. 55k. August 2026 leader: 45k."
    outcome = EvidenceValidator().validate(_result(answer, ids), deps=_deps(store))
    assert "shows no value" not in (outcome.reason or "")


def test_ungrounded_numbers_are_listed_when_revising() -> None:
    store = InMemoryArtifactStore()
    ids = _seed(store)
    answer = "October 2025: Alpha5 ₹55,000. August 2026: nothing sold, 98,765 visits."
    outcome = EvidenceValidator().validate(_result(answer, ids), deps=_deps(store))
    assert outcome.verdict == "REVISE"
    assert "98,765" in (outcome.reason or "")


def test_scan_numbers_reads_grouping_decimals_and_skips_identifiers() -> None:
    found = [(n.text, n.value, n.decimals) for n in scan_numbers("₹1,76,727 and 12.5% in Q3 for SKU12, 3.")]
    assert found == [("1,76,727", 176727.0, 0), ("12.5", 12.5, 1), ("3", 3.0, 0)]
