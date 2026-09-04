"""Coordinator state helpers — MissionState lives in orchestration.state.

This module re-exports MissionState and provides serialization helpers so
control-plane code does not import live clients into LangGraph state.
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.orchestration.state import MissionState


def empty_mission_extensions() -> dict[str, Any]:
    """Default Coordinator V1 fields safe to merge into MissionState."""
    return {
        "status_reason": None,
        "execution_mode": "production",
        "synthetic": False,
        "normalized_query": {},
        "decomposition_refs": [],
        "current_decomposition_ref": None,
        "decompositions": [],
        "objectives": [],
        "claim_refs": [],
        "validated_claim_refs": [],
        "challenged_claim_refs": [],
        "rejected_claim_refs": [],
        "managed_claims": [],
        "evidence_gaps": [],
        "conflicts": [],
        "remediation_round": 0,
        "remediation_tasks": [],
        "budgets": {},
        "usage": {},
        "events": [],
        "completion_detail": {},
        "budget_exhausted": False,
    }


def merge_control_plane(state: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Shallow merge that never injects live dependency objects."""
    forbidden = {"runtime", "llm", "mcp", "transport", "blackboard", "invoker"}
    clean = {k: v for k, v in patch.items() if k not in forbidden and not callable(v)}
    out = dict(state)
    out.update(clean)
    return out


__all__ = ["MissionState", "empty_mission_extensions", "merge_control_plane"]
