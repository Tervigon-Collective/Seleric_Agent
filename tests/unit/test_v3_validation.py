from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
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
        mcp_client=object(),
        artifact_store=artifact_store or InMemoryArtifactStore(),
        limits=limits or ExecutionLimits(),
    )


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
