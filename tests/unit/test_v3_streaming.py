"""Live final-answer streaming through ``run_validated_mission`` (on_stream)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.agent.output import MissionResult
from seleric_swarm.agent.validation import run_validated_mission
from seleric_swarm.conversations.contracts import (
    ContextBundle,
    Principal,
    PrincipalAuthMethod,
)
from seleric_swarm.state.artifacts import InMemoryArtifactStore


def _deps(*, limits: ExecutionLimits | None = None) -> SelericDeps:
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
        artifact_store=InMemoryArtifactStore(),
        limits=limits or ExecutionLimits(),
    )


def _output(final_response: str) -> dict[str, object]:
    return {
        "mission_id": "MS3-test",
        "status": "completed",
        "query": "hello",
        "as_of": datetime.now(UTC),
        "final_response": final_response,
        "evidence_ids": [],
        "finding_ids": [],
        "limitations": [],
        "error_code": None,
        "trace": {},
    }


@pytest.mark.asyncio
async def test_streamed_deltas_reconstruct_final_answer() -> None:
    model = TestModel(custom_output_args=_output("a valid streamed answer"))
    agent = Agent(model=model, deps_type=SelericDeps, output_type=MissionResult)
    chunks: list[str] = []

    def on_stream(kind: str, text: str) -> None:
        if kind == "delta":
            chunks.append(text)

    result = await run_validated_mission(
        agent, _deps(limits=ExecutionLimits(max_validation_revisions=1)), "hello", on_stream=on_stream
    )

    assert result.status == "completed"
    # The concatenated deltas are exactly the final answer — nothing dropped or
    # duplicated by the suffix-diff loop.
    assert "".join(chunks) == "a valid streamed answer"
    assert result.final_response == "a valid streamed answer"


@pytest.mark.asyncio
async def test_reset_emitted_before_a_revision_restreams() -> None:
    # An empty final_response never validates -> the bounded REVISE loop fires,
    # which must emit a reset so the streamed-but-rejected draft is cleared.
    model = TestModel(custom_output_args=_output(""))
    agent = Agent(model=model, deps_type=SelericDeps, output_type=MissionResult)
    kinds: list[str] = []

    result = await run_validated_mission(
        agent,
        _deps(limits=ExecutionLimits(max_validation_revisions=1)),
        "hello",
        on_stream=lambda kind, _text: kinds.append(kind),
    )

    assert result.status == "failed"
    assert "reset" in kinds
