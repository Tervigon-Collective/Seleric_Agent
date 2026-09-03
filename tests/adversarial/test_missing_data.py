import pytest

from seleric_swarm.orchestration.runner import run_mission


@pytest.mark.asyncio
async def test_missing_fixture_date_is_insufficient_evidence_not_zero(runtime):
    result = await run_mission(
        runtime,
        query="What were net sales on 2026-07-15?",
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
