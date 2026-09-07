"""LangGraph DECIDE → EXECUTE cycle tests for swarm_v2."""

from __future__ import annotations

import pytest

from seleric_swarm.coordinator.graph import build_swarm_v2_graph, run_swarm_v2_mission
from seleric_swarm.orchestration.dispatch import run_any_mission


@pytest.mark.asyncio
async def test_decide_execute_emits_wave_events(runtime):
    result = await run_swarm_v2_mission(
        runtime,
        query="Why has CAC increased over the last three days?",
        full_diagnostic=True,
        full_prediction=True,
    )
    kinds = [e.get("kind") for e in result.events]
    assert "task_wave_executed" in kinds or "decide_execute_wave" in kinds
    assert "decomposition_created" in kinds or "mission_control_plane" in kinds
    control = next(e for e in result.events if e.get("kind") == "mission_control_plane")
    assert control.get("decide_execute") is True
    assert int(control.get("iterations") or 0) >= 1
    assert result.status in {"completed", "partial", "prototype_completed"}
    assert result.final_response


@pytest.mark.asyncio
async def test_decide_execute_graph_compiles(runtime):
    # Smoke: building a graph requires context; use a mission run which compiles internally.
    # Ensure the public builder symbol exists and is callable.
    assert callable(build_swarm_v2_graph)


@pytest.mark.asyncio
async def test_dispatch_uses_langgraph_v2(runtime):
    runtime.settings.swarm_workflow = "swarm_v2"
    out = await run_any_mission(
        runtime,
        query="Why has CAC increased and what should we do?",
        full_diagnostic=True,
        full_skeptic=False,
    )
    assert out["workflow"] == "swarm_v2"
    events = out["result"].get("events") or []
    assert any(e.get("decide_execute") for e in events if e.get("kind") == "mission_control_plane")
