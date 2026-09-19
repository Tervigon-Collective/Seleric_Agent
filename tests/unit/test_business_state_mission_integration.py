"""Mission-level integration test: does a real Coordinator mission actually
reach BusinessStateService, not just the module-level facade in isolation?

Everything here is real: live seleric-mcp, live LLM classification, the real
LangGraph DECIDE->EXECUTE cycle (coordinator.graph.run_swarm_v2_mission), and
build_mcp_bundle()'s default providers (which now pass runtime.business_state
through per Sprint 2.5). The one non-real thing is a thin spy wrapped around
BusinessStateService.get_metric_state -- it still calls straight through to
the real implementation, it just also records that the call happened, since
the API/mission result only exposes artifact *ids* (SwarmMissionResult.artifacts
is dict[str, list[str]]), not enough to see which detector produced an
anomaly finding from the outside.
"""

from __future__ import annotations

import pytest

from seleric_swarm.api.status import TERMINAL_STATUSES
from seleric_swarm.coordinator.graph import run_swarm_v2_mission


@pytest.mark.asyncio
async def test_diagnostic_mission_reaches_business_state_for_overridden_metric(runtime):
    calls: list[dict] = []
    original = runtime.business_state.get_metric_state

    async def spy(request):
        state = await original(request)
        calls.append(
            {
                "metric_id": request.metric_id,
                "need": request.need,
                "status": state.status,
                "quality_flags": state.quality_flags,
                "error_code": state.error_code,
                "provenance_error": (state.provenance or {}).get("error"),
            }
        )
        return state

    runtime.business_state.get_metric_state = spy
    try:
        result = await run_swarm_v2_mission(
            runtime,
            query="Why did ad spend increase over the last 3 days?",
            timezone="Asia/Kolkata",
            as_of="2026-09-07",
            execution_mode="production",
            full_diagnostic=True,
        )
    finally:
        runtime.business_state.get_metric_state = original

    assert result.status in TERMINAL_STATUSES
    assert result.error_code != "ROUTING_UNSUPPORTED"

    # provider_selection.py's hardcoded set maps metric.spend -> robust_zscore,
    # so if AnomalyAgent got a metric.spend reading with a real baseline, the
    # dispatch in ConfiguredAnomalyDetector must have called BusinessStateService
    # for it -- this is the thing a bare HTTP-response assertion can't see.
    spend_calls = [c for c in calls if c["metric_id"] == "metric.spend"]
    assert spend_calls, (
        f"BusinessStateService.get_metric_state was never called for metric.spend "
        f"during a real mission; anomaly artifacts: {result.artifacts.get('anomaly')}; "
        f"all business_state calls: {calls}"
    )
    assert all(c["status"] in {"OK", "PARTIAL"} for c in spend_calls), spend_calls
