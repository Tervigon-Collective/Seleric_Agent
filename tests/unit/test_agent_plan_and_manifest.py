"""Unit tests for the plan-first step and the capability manifest.

- capability_manifest() lists every registered tool (incl. run_python).
- build_plan() fails open on the stub model, consumes the Jev intent, and
  returns the model's plan text with a real (non-stub) model.
- _store_plan_artifact writes a ``ui`` plan artifact for observability.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from seleric_swarm.agent import plan as plan_mod
from seleric_swarm.agent import runner
from seleric_swarm.agent.agent import TOOLS, capability_manifest
from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.agent.model import _stub_test_model
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
from seleric_swarm.state.artifacts import InMemoryArtifactStore


def _catalogue() -> CatalogueSnapshot:
    return CatalogueSnapshot(metrics=(CatalogueMetricMeta(id="net_sales", label="Net Sales"),))


def test_manifest_lists_all_tools_including_sandbox() -> None:
    manifest = capability_manifest()
    lines = manifest.splitlines()
    assert lines[0].startswith("Tools available")
    assert len([line for line in lines if line.startswith("- ")]) == len(TOOLS)
    assert any(line.startswith("- run_python:") for line in lines)
    assert any(line.startswith("- query_metrics:") for line in lines)


def test_plan_prompt_includes_intent_and_catalogue() -> None:
    prompt = plan_mod._plan_prompt("why did sales drop", "diagnostic", "MANIFEST", _catalogue())
    assert "diagnostic" in prompt
    assert "net_sales" in prompt
    assert "MANIFEST" in prompt


@pytest.mark.asyncio
async def test_build_plan_fails_open_on_stub_model() -> None:
    result = await plan_mod.build_plan(
        _stub_test_model(), query="q", intent="lookup", manifest="M", catalogue=_catalogue()
    )
    assert result is None


@pytest.mark.asyncio
async def test_build_plan_returns_model_text() -> None:
    def _func(messages, info):
        return ModelResponse(parts=[TextPart(content="1. query_metrics net_sales")])

    model = FunctionModel(_func)
    result = await plan_mod.build_plan(
        model, query="net sales", intent="lookup", manifest="M", catalogue=_catalogue()
    )
    assert result == "1. query_metrics net_sales"


@pytest.mark.asyncio
async def test_build_plan_fails_open_on_error() -> None:
    def _boom(messages, info):
        raise RuntimeError("planner down")

    result = await plan_mod.build_plan(
        FunctionModel(_boom), query="q", intent=None, manifest="M", catalogue=_catalogue()
    )
    assert result is None


def test_store_plan_artifact_writes_ui_artifact() -> None:
    store = InMemoryArtifactStore()
    deps = SelericDeps(
        mission_id="m1",
        as_of=datetime(2026, 9, 18, tzinfo=UTC),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"),
        thread_id="t1",
        run_id="r1",
        trace_id="tr1",
        context=ContextBundle(),
        mcp_client=NullMcpClient(),
        artifact_store=store,
        limits=ExecutionLimits(),
    )
    runner._store_plan_artifact(deps, plan="1. do a thing", intent="lookup")
    found = store.list_for_mission("m1")
    assert len(found) == 1
    artifact = found[0]
    assert artifact.artifact_type == "plan"
    assert artifact.classification == "ui"
    assert artifact.payload["plan"] == "1. do a thing"
    assert artifact.payload["intent"] == "lookup"
