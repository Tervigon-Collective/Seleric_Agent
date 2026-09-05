"""Dispatch routing regressions for lookup vs swarm."""

from __future__ import annotations

import pytest

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
        ("Show me how many orders dropped yesterday", "swarm"),
        ("how many sessions fell last week?", "swarm"),
        ("tell me what the blended CAC was on 2026-09-02", "lookup"),
        ("What were backdrop sales yesterday?", "lookup"),
        ("Compare Meta vs Google CAC increase over three days", "lookup"),
    ],
)
async def test_route_for_lookup_vs_swarm(query, expected):
    assert await route_for(None, query=query) == expected  # type: ignore[arg-type]
