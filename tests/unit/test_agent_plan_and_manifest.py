"""Unit tests for the plan-first step and the capability manifest.

- capability_manifest() lists every registered tool (incl. run_python).
- build_plan(): the model fills PlanSlots, the catalogue resolver and the
  per-shape templates build the plan; fails open on the stub model and errors.
- _store_plan_artifact writes a ``ui`` plan artifact for observability.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from seleric_swarm.agent import plan as plan_mod
from seleric_swarm.agent import runner
from seleric_swarm.agent.agent import TOOLS, capability_manifest
from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.agent.model import _stub_test_model
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.services import elapsed
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
from seleric_swarm.state.artifacts import InMemoryArtifactStore


def _catalogue() -> CatalogueSnapshot:
    return CatalogueSnapshot(
        metrics=(
            CatalogueMetricMeta(
                id="net_sales", label="Net Sales", supported_dimensions=["channel"], raw={"aggregation": "additive"}
            ),
            CatalogueMetricMeta(
                id="ad_spend", label="Ad spend", supported_dimensions=["ad_name"], raw={"aggregation": "additive"}
            ),
            CatalogueMetricMeta(
                id="roas",
                label="ROAS",
                supported_dimensions=["ad_name"],
                raw={"aggregation": "ratio", "volume_metric": "ad_spend"},
            ),
        ),
        dimensions=("ad_name", "channel"),
    )


def _slots_model(slots: dict) -> FunctionModel:
    """A model that answers with ``slots`` through the structured output tool."""

    def _func(messages, info):
        return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=slots)])

    return FunctionModel(_func)


_TOOLS = frozenset({"query_metrics", "diagnose_metric_change"})
IST = ZoneInfo("Asia/Kolkata")
_TODAY = datetime(2026, 10, 7, tzinfo=IST)
_WINDOWS = [(date(2026, 10, 4), date(2026, 10, 6)), (date(2026, 10, 7), date(2026, 10, 7))]

_ENTITY_SLOTS = {
    "shape": "entity_comparison",
    "entity_dimension": "ad_name",
    "rank_by": {"words": "return on ad spend", "metric_id": "roas"},
    "metrics": [{"words": "spend", "metric_id": "ad_spend"}, {"words": "return on ad spend", "metric_id": "roas"}],
    "breakdown_dimensions": [],
}


def test_manifest_lists_all_tools_including_sandbox() -> None:
    manifest = capability_manifest()
    lines = manifest.splitlines()
    assert lines[0].startswith("Tools available")
    assert len([line for line in lines if line.startswith("- ")]) == len(TOOLS)
    assert any(line.startswith("- run_python:") for line in lines)
    assert any(line.startswith("- query_metrics:") for line in lines)


def test_slot_prompt_includes_intent_and_catalogue() -> None:
    prompt = plan_mod._slot_prompt("why did sales drop", "diagnostic", _catalogue())
    assert "diagnostic" in prompt and "net_sales" in prompt


@pytest.mark.asyncio
async def test_build_plan_fails_open_on_stub_model() -> None:
    outcome = await plan_mod.build_plan(
        _stub_test_model(), query="q", intent="lookup", manifest="M", catalogue=_catalogue()
    )
    assert outcome.plan is None and outcome.text is None
    assert outcome.stats["status"] == "skipped"


async def _plan(slots: dict, *, resolver=None, now=datetime(2026, 10, 7, 12, 30, tzinfo=IST), monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(elapsed, "now_in", lambda tz: now)
    return await plan_mod.build_plan(
        _slots_model(slots),
        query="best ads last 3 days vs today",
        intent="comparison",
        manifest="M",
        catalogue=_catalogue(),
        tool_names=_TOOLS,
        resolver=resolver,
        windows=_WINDOWS,
        as_of=_TODAY,
    )


@pytest.mark.asyncio
async def test_an_entity_comparison_ranks_then_compares_the_same_entities_over_the_same_hours(monkeypatch) -> None:
    outcome = await _plan(_ENTITY_SLOTS, monkeypatch=monkeypatch)
    plan = outcome.plan
    assert plan is not None and plan.shape == "entity_comparison"
    rank, reference, today = plan.steps[:3]
    assert rank.metric_ids == ["roas"] and rank.dimensions == ["ad_name"] and "desc" in rank.ranking
    assert "meaningful ad_spend" in rank.ranking
    assert reference.period == "2026-10-04..2026-10-06" and today.period == "2026-10-07"
    assert reference.uses_entities_from_step == 1 and today.uses_entities_from_step == 1
    assert "elapsed_only=True in BOTH windows" in today.purpose
    assert outcome.text.startswith("Advisory plan")


@pytest.mark.asyncio
async def test_slots_with_an_entity_and_a_rank_make_an_entity_comparison(monkeypatch) -> None:
    outcome = await _plan({**_ENTITY_SLOTS, "shape": "period_comparison"}, monkeypatch=monkeypatch)
    assert outcome.plan is not None and outcome.plan.shape == "entity_comparison"


@pytest.mark.asyncio
async def test_the_catalogue_resolver_wins_over_the_model_guess(monkeypatch) -> None:
    async def resolver(words):
        return {w: ("net_sales" if w == "sales" else None) for w in words}

    slots = {**_ENTITY_SLOTS, "metrics": [{"words": "sales", "metric_id": "ad_spend"}, {"words": "x", "metric_id": "nope"}]}
    outcome = await _plan(slots, resolver=resolver, monkeypatch=monkeypatch)
    assert "net_sales" in outcome.stats["metrics"] and "ad_spend" not in outcome.stats["metrics"]
    assert any("no catalogue metric for 'x'" in n for n in outcome.stats["notes"])


@pytest.mark.asyncio
async def test_a_why_question_about_a_running_day_is_compared_not_diagnosed(monkeypatch) -> None:
    slots = {"shape": "why_single_metric", "metrics": [{"words": "sales", "metric_id": "net_sales"}]}
    outcome = await _plan(slots, monkeypatch=monkeypatch)
    assert outcome.plan.shape == "period_comparison"
    assert all(step.tool == "query_metrics" for step in outcome.plan.steps)


@pytest.mark.asyncio
async def test_unsupported_dimensions_are_pruned_and_named(monkeypatch) -> None:
    outcome = await _plan({**_ENTITY_SLOTS, "breakdown_dimensions": ["channel", "made_up"]}, monkeypatch=monkeypatch)
    notes = " ".join(outcome.stats["notes"])
    assert "no dimension made_up" in notes
    assert outcome.plan is not None


@pytest.mark.asyncio
async def test_build_plan_fails_open_on_error() -> None:
    def _boom(messages, info):
        raise RuntimeError("planner down")

    outcome = await plan_mod.build_plan(
        FunctionModel(_boom), query="q", intent=None, manifest="M", catalogue=_catalogue()
    )
    assert outcome.plan is None
    assert outcome.stats["status"] == "failed"


def test_a_namespaced_tool_name_is_read_as_the_registered_tool() -> None:
    plan = plan_mod.MissionPlan(
        shape="lookup", steps=[plan_mod.PlanStep(tool="functions.query_metrics", purpose="fetch it")]
    )
    cleaned, notes = plan_mod.sanitize_plan(plan, tool_names=_TOOLS, catalogue=_catalogue())
    assert cleaned.steps[0].tool == "query_metrics" and not notes


def test_plan_adherence_counts_planned_steps_the_mission_ran() -> None:
    plan = plan_mod.MissionPlan(
        shape="lookup", steps=[plan_mod.PlanStep(tool="query_metrics", metric_ids=["ad_spend"], purpose="fetch it")]
    )
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

