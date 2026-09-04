import pytest

from seleric_swarm.orchestration.runner import run_mission


@pytest.mark.asyncio
async def test_empty_day_does_not_fabricate_evidence_when_missing(runtime):
    # CAC has sparse days; a far-past date should fail closed rather than invent a number.
    result = await run_mission(
        runtime,
        query="What is CAC on 2020-01-01?",
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "INSUFFICIENT_EVIDENCE"
    assert all(row.value not in {0} for row in result.evidence)
    assert result.limitations


@pytest.mark.asyncio
async def test_write_mode_rejected(runtime):
    result = await run_mission(runtime, query="What were net sales yesterday?", mode="write")
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "INVALID_REQUEST"
