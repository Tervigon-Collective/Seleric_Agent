"""Structured mission event taxonomy tests."""

from __future__ import annotations

import pytest

from seleric_swarm.coordinator.graph import run_swarm_v2_mission
from seleric_swarm.coordinator.observability.events import (
    MissionEventEmitter,
    assert_lifecycle_coverage,
    canonical_kind,
    summarize_event_families,
)
from seleric_swarm.swarm.blackboard import Blackboard


def test_canonical_kind_aliases():
    assert canonical_kind("decide_execute_wave") == "task_wave_executed"
    assert canonical_kind("budget_exhausted") == "mission_budget_exhausted"
    assert canonical_kind("decomposition_created") == "decomposition_created"


def test_emitter_envelope():
    bb = Blackboard("MS-test")
    emitter = MissionEventEmitter(bb, workflow_version="1.3.0")
    event = emitter.emit("plan_created", tasks=3)
    assert event["kind"] == "task_plan_created"
    assert event["legacy_kind"] == "plan_created"
    assert event["mission_id"] == "MS-test"
    assert event["seq"] == 1
    assert event["ts"].endswith("Z")
    assert event["family"] == "task"
    assert event["workflow_version"] == "1.3.0"


@pytest.mark.asyncio
async def test_swarm_v2_event_taxonomy_coverage(runtime):
    result = await run_swarm_v2_mission(
        runtime,
        scenario_id="cac_regression",
        query="Why has CAC increased over the last three days?",
        full_diagnostic=True,
    )
    missing = assert_lifecycle_coverage(result.events)
    assert missing == [], f"missing event families: {missing}"

    families = summarize_event_families(result.events)
    assert families["mission"] >= 1
    assert families["decomposition"] >= 1
    assert families["task"] >= 1
    assert families["artifact"] >= 1  # evidence posts
    assert families["skeptic"] >= 1 or families["claim"] >= 1

    # Envelope present on control-plane events
    control = next(e for e in result.events if e.get("kind") == "mission_control_plane")
    assert control.get("seq")
    assert control.get("ts")
    assert control.get("mission_id") == result.mission_id
    assert control.get("event_families")

    kinds = {e.get("kind") for e in result.events}
    assert "mission_created" in kinds
    assert "task_wave_executed" in kinds
    assert "mission_completed" in kinds or "mission_partial" in kinds
