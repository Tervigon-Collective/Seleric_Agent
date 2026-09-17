"""Dispatch routing regressions for lookup vs swarm.

route_for is LLM-classification-driven (coordinator.classify_swarm), so these
run against the `runtime` fixture's fake LLM adapter rather than calling it
with no runtime.
"""

from __future__ import annotations

import pytest

from seleric_swarm.orchestration import dispatch
from seleric_swarm.orchestration.dispatch import route_for


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,expected",
    [
        ("What were net sales yesterday?", "lookup"),
        ("How many orders did we get on 2026-09-02?", "lookup"),
        ("Compare Meta vs Google CAC over the last three days", "lookup"),
        ("Why has CAC increased over the last three days?", "swarm"),
        ("how are we doing today?", "swarm"),
        ("what happens if this continues?", "swarm"),
        ("what should we do about rising CAC?", "swarm"),
        ("Explain the root cause of mobile LCP degradation", "swarm"),
        ("tell me what the blended CAC was on 2026-09-02", "lookup"),
        ("Compare Meta vs Google CAC increase over three days", "lookup"),
    ],
)
async def test_route_for_lookup_vs_swarm(runtime, query, expected):
    assert await route_for(runtime, query=query) == expected


@pytest.mark.asyncio
async def test_greeting_completes_without_classifier_or_swarm(runtime, monkeypatch):
    async def fail_route(*args, **kwargs):
        raise AssertionError("a greeting must not invoke business routing")

    monkeypatch.setattr(dispatch, "route_for", fail_route)

    response = await dispatch.run_any_mission(
        runtime,
        query="Hi!",
        mission_id="MS-greeting",
        request_id="request-greeting",
        session_id="thread-greeting",
    )

    assert response["route"] == "conversation"
    assert response["result"]["status"] == "completed"
    assert "What would you like to investigate?" in response["result"]["final_response"]
    assert runtime.store.get("MS-greeting").status == "completed"
