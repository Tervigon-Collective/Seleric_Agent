from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.agent.output import MissionResult
from seleric_swarm.agent.validation import EvidenceValidator, run_validated_mission
from seleric_swarm.conversations.contracts import ContextBundle, Principal, PrincipalAuthMethod
from seleric_swarm.state.artifacts import InMemoryArtifactStore


def _deps(*, artifact_store=None, limits: ExecutionLimits | None = None) -> SelericDeps:
    return SelericDeps(
        mission_id="MS3-test",
        as_of=datetime.now(UTC),
        principal=Principal(
            principal_id="p1",
            workspace_id="ws-1",
            user_id="user-1",
            authenticated=False,
            auth_method=PrincipalAuthMethod.ANONYMOUS,
        ),
        thread_id="thread-1",
        run_id="run-1",
        trace_id="trace-1",
        context=ContextBundle(),
        mcp_client=NullMcpClient(),
        artifact_store=artifact_store or InMemoryArtifactStore(),
        limits=limits or ExecutionLimits(),
    )


def _seed_daily_evidence(
    store: InMemoryArtifactStore, mission_id: str, *, count: int = 10
) -> list[str]:
    """Enough clean single-day evidence to clear the row floor and score well."""
    from seleric_swarm.agent.artifacts import EvidenceArtifact
    from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance

    ids: list[str] = []
    for day in range(1, count + 1):
        stamp = datetime(2026, 9, day, tzinfo=UTC)
        payload = EvidenceArtifact(
            metric_id="metric.net_sales",
            grain="day",
            as_of=datetime.now(UTC),
            period_start=stamp,
            period_end=stamp,
            value=100.0 + day,
            source_query={"measure": "metric.net_sales"},
        )
        artifact = store.put(
            Artifact(
                workspace_id="ws-1",
                artifact_type="evidence",
                payload=payload.model_dump(mode="json"),
                classification="factual",
                evidence_ids=[f"raw:net_sales:{day}"],
                provenance=ArtifactProvenance(query_version="q1"),
                mission_id=mission_id,
            )
        )
        ids.append(artifact.id)
    return ids


def _put_derived(
    store: InMemoryArtifactStore,
    mission_id: str,
    artifact_type: str,
    payload: dict,
    evidence_ids: list[str],
) -> str:
    from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance

    return store.put(
        Artifact(
            workspace_id="ws-1",
            artifact_type=artifact_type,
            payload=payload,
            classification="derived",
            evidence_ids=evidence_ids,
            provenance=ArtifactProvenance(
                evidence_ids=evidence_ids, calculation_version=f"{artifact_type}.v0"
            ),
            mission_id=mission_id,
        )
    ).id


def _result(**overrides: object) -> MissionResult:
    base: dict[str, object] = {
        "mission_id": "MS3-test",
        "status": "completed",
        "query": "hello",
        "as_of": datetime.now(UTC),
        "final_response": "ok",
        "evidence_ids": [],
        "finding_ids": [],
    }
    base.update(overrides)
    return MissionResult.model_validate(base)


def test_validate_passes_with_no_referenced_artifacts() -> None:
    validator = EvidenceValidator()
    outcome = validator.validate(_result(), deps=_deps())
    assert outcome.ok


def test_validate_fails_on_unresolved_artifact_id() -> None:
    validator = EvidenceValidator()
    outcome = validator.validate(_result(evidence_ids=["ev-missing"]), deps=_deps())
    assert not outcome.ok
    assert "ev-missing" in (outcome.reason or "")


@pytest.mark.parametrize("text", ["placeholder", "  Placeholder. ", "TBD", "n/a", "..."])
def test_validate_rejects_a_placeholder_answer(text: str) -> None:
    outcome = EvidenceValidator().validate(_result(final_response=text), deps=_deps())
    assert not outcome.ok


def test_validate_accepts_a_short_real_answer() -> None:
    assert EvidenceValidator().validate(_result(final_response="You're welcome!"), deps=_deps()).ok


def test_validate_fails_on_empty_final_response_when_completed() -> None:
    validator = EvidenceValidator()
    outcome = validator.validate(_result(final_response=""), deps=_deps())
    assert not outcome.ok


@pytest.mark.asyncio
async def test_run_validated_mission_passes_through_a_valid_result() -> None:
    model = TestModel(
        custom_output_args={
            "mission_id": "MS3-test",
            "status": "completed",
            "query": "hello",
            "as_of": datetime.now(UTC),
            "final_response": "a valid answer",
            "evidence_ids": [],
            "finding_ids": [],
            "limitations": [],
            "error_code": None,
            "trace": {},
        }
    )
    agent = Agent(model=model, deps_type=SelericDeps, output_type=MissionResult)
    deps = _deps(limits=ExecutionLimits(max_validation_revisions=1))
    result = await run_validated_mission(agent, deps, "hello")
    assert result.status == "completed"
    assert result.error_code is None


@pytest.mark.asyncio
async def test_run_validated_mission_streams_tool_progress_when_a_sink_is_registered() -> None:
    from seleric_swarm.agent import progress

    model = TestModel(
        custom_output_args={
            "mission_id": "MS3-test",
            "status": "completed",
            "query": "hello",
            "as_of": datetime.now(UTC),
            "final_response": "a valid answer",
            "evidence_ids": [],
            "finding_ids": [],
            "limitations": [],
            "error_code": None,
            "trace": {},
        }
    )
    agent = Agent(model=model, deps_type=SelericDeps, output_type=MissionResult)

    @agent.tool_plain
    def search_semantics() -> str:
        return "5 metrics"

    seen: list[tuple[str, str]] = []
    progress.set_progress_sink("MS3-test", lambda et, summary, _payload: seen.append((et, summary)))
    try:
        result = await run_validated_mission(agent, _deps(), "hello")
    finally:
        progress.clear_progress_sink("MS3-test")

    assert result.status == "completed"
    assert result.final_response == "a valid answer"
    assert ("agent.tool_started", "Searching the metric catalogue") in seen
    assert ("agent.tool_completed", "Searching the metric catalogue — done") in seen
    assert seen[-1] == ("agent.answering", "Writing the answer")


@pytest.mark.asyncio
async def test_run_validated_mission_fails_after_exhausting_bounded_retries() -> None:
    # Always returns an empty final_response on a "completed" mission --
    # never validates -- to prove the retry loop is bounded (rule 11), not
    # infinite, and surfaces INSUFFICIENT_EVIDENCE once exhausted.
    model = TestModel(
        custom_output_args={
            "mission_id": "MS3-test",
            "status": "completed",
            "query": "hello",
            "as_of": datetime.now(UTC),
            "final_response": "",
            "evidence_ids": [],
            "finding_ids": [],
            "limitations": [],
            "error_code": None,
            "trace": {},
        }
    )
    agent = Agent(model=model, deps_type=SelericDeps, output_type=MissionResult)
    deps = _deps(limits=ExecutionLimits(max_validation_revisions=1))
    result = await run_validated_mission(agent, deps, "hello")
    assert result.status == "failed"
    assert result.error_code == "INSUFFICIENT_EVIDENCE"
    assert "live metric evidence" in result.final_response


def test_usage_limits_follow_tool_call_cap() -> None:
    from seleric_swarm.agent.validation import _usage_limits

    limits = _usage_limits(ExecutionLimits(max_tool_calls=4))
    assert limits.tool_calls_limit == 4
    assert limits.request_limit == 36


def test_validate_passes_causal_artifact_with_valid_classification() -> None:
    """A1.4 vocabulary gate: a frozen-vocabulary classification passes.

    Updated in Sprint 3. This originally stored a lone causal artifact citing
    ``evidence_ids=["ev-1"]`` with no such artifact in the store, and passed
    only because validation was structural. The content checks correctly flag
    that as a dangling provenance reference (rule 6) — a blocking gap, so
    REVISE. The test's intent is the vocabulary gate, so it now supplies the
    backing evidence the causal claim says it has.
    """
    from seleric_swarm.agent.artifacts import CausalArtifact

    store = InMemoryArtifactStore()
    deps = _deps(artifact_store=store)
    evidence_ids = _seed_daily_evidence(store, deps.mission_id)
    causal = CausalArtifact(
        query={"treatment": "t", "outcome": "o"},
        evidence_classification="ASSOCIATION",
        effect_estimate=None,
        refutation_checks=[],
        evidence_ids=evidence_ids,
        method="backdoor.linear_regression",
    )
    _put_derived(store, deps.mission_id, "causal", causal.model_dump(mode="json"), evidence_ids)

    outcome = EvidenceValidator().validate(_result(), deps=deps)

    assert outcome.ok, outcome.reason
    # Both signals present and independent — the #12 property.
    assert outcome.verdict == "PASS"
    assert outcome.trust_label is not None


def test_validate_fails_causal_artifact_with_legacy_classification() -> None:
    """A1.4: legacy UNDER_ASSUMPTIONS / ASSOCIATION_ONLY must not pass."""
    from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance

    store = InMemoryArtifactStore()
    deps = _deps(artifact_store=store)
    store.put(
        Artifact(
            workspace_id="ws-1",
            artifact_type="causal",
            payload={
                "query": {"treatment": "t", "outcome": "o"},
                "evidence_classification": "CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS",
                "effect_estimate": 1.0,
                "refutation_checks": [{"name": "x", "passed": True}],
                "evidence_ids": ["ev-1"],
                "method": "backdoor.linear_regression",
            },
            classification="derived",
            evidence_ids=["ev-1"],
            provenance=ArtifactProvenance(calculation_version="causal.v0"),
            mission_id=deps.mission_id,
        )
    )
    outcome = EvidenceValidator().validate(_result(), deps=deps)
    assert not outcome.ok
    assert "evidence_classification" in (outcome.reason or "")
