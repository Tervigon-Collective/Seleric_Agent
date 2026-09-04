"""Coordinator LangSmith task-span metadata (v1.10 / workflow 1.4.0)."""

from __future__ import annotations

from seleric_swarm.observability.tracing import (
    REQUIRED_SPAN_METADATA,
    coordinator_task_metadata,
    missing_required_metadata,
)


def test_coordinator_task_metadata_includes_plan_fields():
    meta = coordinator_task_metadata(
        request_id="r1",
        session_id="s1",
        mission_id="MS-1",
        agent_name="diagnostic_agent",
        task_id="T-1",
        subquestion_id="SQ-1",
        active_specialist="diagnostic_agent",
        mission_lead="performance_agent",
        remediation_round=2,
        decomposition_id="DEC-1-v2",
        decomposition_version=2,
        leadership_epoch=1,
        synthetic=True,
    )
    assert missing_required_metadata(meta, REQUIRED_SPAN_METADATA) == []
    assert meta["task_id"] == "T-1"
    assert meta["subquestion_id"] == "SQ-1"
    assert meta["active_specialist"] == "diagnostic_agent"
    assert meta["mission_lead"] == "performance_agent"
    assert meta["remediation_round"] == 2
    assert meta["decomposition_id"] == "DEC-1-v2"
    assert meta["decomposition_version"] == 2
    assert meta["leadership_epoch"] == 1
    assert meta["synthetic"] is True
    assert meta["workflow_version"] == "1.4.0"


def test_coordinator_task_metadata_omits_none_optionals():
    meta = coordinator_task_metadata(
        request_id="r1",
        session_id="s1",
        mission_id="MS-1",
        agent_name="observer_agent",
    )
    assert "task_id" not in meta
    assert "subquestion_id" not in meta
    assert meta["active_specialist"] == "observer_agent"
