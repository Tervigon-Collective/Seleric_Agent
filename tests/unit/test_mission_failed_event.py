"""Terminal mission_failed events on unsupported / error finalize paths."""

from __future__ import annotations

from seleric_swarm.orchestration.graph import _emit_lookup_event


def test_emit_lookup_event_mission_failed_kind():
    state = {"mission_id": "M-fail", "events": [], "workflow_version": "1.0.0"}
    patch = _emit_lookup_event(state, "mission_failed", status="failed", error_code="ROUTING_UNSUPPORTED")
    events = patch["events"]
    assert len(events) == 1
    assert events[0]["kind"] == "mission_failed"
    assert events[0]["status"] == "failed"
    assert events[0]["family"] == "mission"
