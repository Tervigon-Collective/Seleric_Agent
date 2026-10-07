"""Unit tests for the plan-first step and the capability manifest.

- capability_manifest() lists every registered tool (incl. run_python).
- build_plan() fails open on the stub model, on errors and on a plan with no
  usable step; what the catalogue lacks is pruned and named in the advisory block.
- _store_plan_artifact writes a ``ui`` plan artifact for observability.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
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
    return CatalogueSnapshot(
        metrics=(
            CatalogueMetricMeta(id="net_sales", label="Net Sales", supported_dimensions=["channel"]),
            CatalogueMetricMeta(id="ad_spend", label="Ad spend", supported_dimensions=["ad_name"]),
        ),
        dimensions=("ad_name", "channel"),
    )


def _plan_model(plan: dict) -> FunctionModel:
    """A model that answers with ``plan`` through the structured output tool."""

    def _func(messages, info):
        return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=plan)])

    return FunctionModel(_func)


_TOOLS = frozenset({"query_metrics", "diagnose_metric_change"})


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
    outcome = await plan_mod.build_plan(
        _stub_test_model(), query="q", intent="lookup", manifest="M", catalogue=_catalogue()
    )
    assert outcome.plan is None and outcome.text is None
    assert outcome.stats["status"] == "skipped"


_ENTITY_PLAN = {
    "shape": "entity_comparison",
    "steps": [
        {
            "tool": "query_metrics",
            "metric_ids": ["ad_spend"],
            "dimensions": ["ad_name"],
            "period": "last 3 complete days",
            "ranking": "top 5 by spend",
            "purpose": "rank the ads over the reference window",
        },
        {
            "tool": "query_metrics",
            "metric_ids": ["ad_spend"],
            "dimensions": ["ad_name"],
            "period": "today, elapsed hours",
            "uses_entities_from_step": 1,
            "purpose": "the same ads today",
        },
    ],
}


@pytest.mark.asyncio
async def test_build_plan_returns_a_validated_advisory_plan() -> None:
    outcome = await plan_mod.build_plan(
        _plan_model(_ENTITY_PLAN),
        query="best ads last 3 days vs today",
        intent="comparison",
        manifest="M",
        catalogue=_catalogue(),
        tool_names=_TOOLS,
    )
    assert outcome.plan is not None and outcome.plan.shape == "entity_comparison"
    assert outcome.stats["status"] == "valid" and outcome.stats["steps"] == 2
    assert "latency_ms" in outcome.stats
    assert outcome.text.startswith("Advisory plan")
    assert "same entities as step 1" in outcome.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "note", "kept"),
    [
        ({"metric_ids": ["made_up_metric"]}, "no metric made_up_metric", {"metric_ids": []}),
        ({"dimensions": ["made_up_dim"]}, "no dimension made_up_dim", {"dimensions": []}),
        ({"dimensions": ["channel"]}, "ad_spend cannot be broken down by channel", {"dimensions": []}),
    ],
)
async def test_what_the_catalogue_lacks_is_pruned_and_named(change: dict, note: str, kept: dict) -> None:
    plan = {"shape": "breakdown", "steps": [{**_ENTITY_PLAN["steps"][0], **change}]}
    outcome = await plan_mod.build_plan(
        _plan_model(plan), query="q", intent=None, manifest="M", catalogue=_catalogue(), tool_names=_TOOLS
    )
    assert outcome.stats["status"] == "pruned"
    assert note in outcome.stats["pruned"][0]
    assert outcome.plan is not None
    step = outcome.plan.steps[0]
    for key, value in kept.items():
        assert getattr(step, key) == value
    assert note in outcome.text


@pytest.mark.asyncio
async def test_a_step_with_an_unknown_tool_is_dropped_and_references_renumbered() -> None:
    plan = {
        "shape": "entity_comparison",
        "steps": [{**_ENTITY_PLAN["steps"][0], "tool": "invented_tool"}, *_ENTITY_PLAN["steps"]],
    }
    plan["steps"][2] = {**plan["steps"][2], "uses_entities_from_step": 2}
    outcome = await plan_mod.build_plan(
        _plan_model(plan), query="q", intent=None, manifest="M", catalogue=_catalogue(), tool_names=_TOOLS
    )
    assert outcome.plan is not None and len(outcome.plan.steps) == 2
    assert outcome.plan.steps[1].uses_entities_from_step == 1
    assert "no tool named invented_tool" in outcome.stats["pruned"][0]


@pytest.mark.asyncio
async def test_a_plan_with_no_usable_step_is_dropped() -> None:
    plan = {"shape": "other", "steps": [{**_ENTITY_PLAN["steps"][0], "tool": "invented_tool"}]}
    outcome = await plan_mod.build_plan(
        _plan_model(plan), query="q", intent=None, manifest="M", catalogue=_catalogue(), tool_names=_TOOLS
    )
    assert outcome.plan is None and outcome.text is None
    assert outcome.stats["status"] == "invalid"


@pytest.mark.asyncio
async def test_a_forward_entity_reference_is_cleared() -> None:
    plan = {"shape": "entity_comparison", "steps": [{**_ENTITY_PLAN["steps"][0], "uses_entities_from_step": 1}]}
    outcome = await plan_mod.build_plan(
        _plan_model(plan), query="q", intent=None, manifest="M", catalogue=_catalogue(), tool_names=_TOOLS
    )
    assert outcome.plan is not None and outcome.plan.steps[0].uses_entities_from_step is None


@pytest.mark.asyncio
async def test_build_plan_fails_open_on_error() -> None:
    def _boom(messages, info):
        raise RuntimeError("planner down")

    outcome = await plan_mod.build_plan(
        FunctionModel(_boom), query="q", intent=None, manifest="M", catalogue=_catalogue()
    )
    assert outcome.plan is None
    assert outcome.stats["status"] == "failed"


def test_plan_adherence_counts_planned_steps_the_mission_ran() -> None:
    plan = plan_mod.MissionPlan.model_validate(_ENTITY_PLAN)
    ran = [{"kind": "tool_call", "tool": "query_metrics", "args": '{"metric_id": "ad_spend"}'}]
    assert plan_mod.plan_adherence(plan, ran) == 1.0
    other = [{"kind": "tool_call", "tool": "query_metrics", "args": '{"metric_id": "net_sales"}'}]
    assert plan_mod.plan_adherence(plan, other) == 0.0
    assert plan_mod.plan_adherence(plan, None) is None


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
