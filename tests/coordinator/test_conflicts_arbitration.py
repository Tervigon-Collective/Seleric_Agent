"""Richer conflict detection + deterministic arbitration tests."""

from __future__ import annotations

from seleric_swarm.coordinator.governance.completion_gate import decide_completion
from seleric_swarm.coordinator.governance.conflicts import (
    arbitrate_conflict,
    arbitrate_conflicts,
    conflict_limitations,
    detect_conflicts,
    unresolved_blocking,
)
from seleric_swarm.coordinator.synthesis.response_builder import build_claim_aware_response
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.mission import SwarmMission


def test_metric_semantic_conflict_prefers_normalized_primary():
    conflicts = detect_conflicts(
        {
            "normalized_query": {"primary_metric": "metric.blended_paid_cac"},
            "evidence": [
                {"artifact_id": "EV-1", "metric_or_fact": "metric.cac", "value": 780},
                {"artifact_id": "EV-2", "metric_or_fact": "metric.blended_paid_cac", "value": 782},
            ],
        }
    )
    semantic = [c for c in conflicts if c["type"] == "METRIC_SEMANTIC_CONFLICT"]
    assert semantic
    resolved = arbitrate_conflict(semantic[0])
    assert resolved["resolved"] is True
    assert resolved["resolution"]["winner"] == "metric.blended_paid_cac"


def test_data_contradiction_prefers_non_synthetic():
    conflicts = detect_conflicts(
        {
            "evidence": [
                {
                    "artifact_id": "EV-real",
                    "metric_or_fact": "metric.cac",
                    "value": 700,
                    "dimensions": {},
                    "time_range": {"start": "a", "end": "b"},
                    "data_origin": "MCP",
                    "synthetic": False,
                },
                {
                    "artifact_id": "EV-fix",
                    "metric_or_fact": "metric.cac",
                    "value": 999,
                    "dimensions": {},
                    "time_range": {"start": "a", "end": "b"},
                    "data_origin": "FIXTURE",
                    "synthetic": True,
                },
            ]
        }
    )
    data = next(c for c in conflicts if c["type"] == "DATA_CONTRADICTION")
    resolved = arbitrate_conflict(data)
    assert resolved["resolved"] is True
    assert resolved["resolution"]["winner"] == "EV-real"


def test_methodology_conflict_strategy_vs_technical_diagnosis():
    conflicts = detect_conflicts(
        {
            "hypotheses": [
                {
                    "artifact_id": "HYP-1",
                    "status": "retained",
                    "statement": "Frontend checkout regression caused mobile CVR drop",
                }
            ],
            "strategies": [
                {
                    "artifact_id": "STRAT-1",
                    "recommended": ["Reduce Meta budget by 20%"],
                    "rationale": "Cut ad spend to lower CAC",
                }
            ],
        }
    )
    method = [c for c in conflicts if c["type"] == "METHODOLOGY_CONFLICT"]
    assert method
    resolved = arbitrate_conflicts(method)[0]
    assert resolved["accepted_as_limitation"] is True
    assert "reject_strategy" in resolved["resolution"]["action"]


def test_model_conflict_rejects_invalid_forecast():
    conflicts = detect_conflicts(
        {
            "predictions": [
                {
                    "artifact_id": "PRED-1",
                    "target": "metric.cac",
                    "prediction": 900,
                    "drift_status": "invalid",
                    "model": {},
                }
            ]
        }
    )
    model = next(c for c in conflicts if c["type"] == "MODEL_CONFLICT")
    resolved = arbitrate_conflict(model)
    assert resolved["accepted_as_limitation"] is True


def test_causal_conflict_stays_unresolved_for_skeptic():
    conflicts = detect_conflicts(
        {
            "hypotheses": [
                {"artifact_id": "H1", "status": "retained", "statement": "Cause A"},
                {"artifact_id": "H2", "status": "retained", "statement": "Cause B"},
            ]
        }
    )
    causal = next(c for c in conflicts if c["type"] == "CAUSAL_CONFLICT")
    resolved = arbitrate_conflict(causal)
    assert resolved["resolved"] is False
    assert unresolved_blocking([resolved])


def test_completion_blocks_on_unresolved_causal_only():
    decision = decide_completion(
        {
            "objectives": [{"objective_id": "O1", "status": "satisfied"}],
            "validated_claim_refs": ["CL-1"],
            "challenged_claim_refs": [],
            "rejected_claim_refs": [],
            "evidence_gaps": [],
            "conflicts": [
                {
                    "conflict_id": "CF-1",
                    "type": "CAUSAL_CONFLICT",
                    "blocking": True,
                    "resolved": False,
                    "description": "two hyps",
                }
            ],
            "tasks": [],
            "evidence": [{"x": 1}],
            "claims": [{"gate_status": "passed"}],
            "status": "completed",
            "skeptic_findings": [{"status": "passed"}],
        }
    )
    assert decision.complete is False
    assert decision.unresolved_conflicts


def test_time_range_conflict_does_not_block_after_arbitration():
    conflicts = arbitrate_conflicts(
        detect_conflicts(
            {
                "evidence": [
                    {
                        "artifact_id": "EV-1",
                        "metric_or_fact": "metric.cac",
                        "value": 1,
                        "time_range": {"start": "2026-01-01", "end": "2026-01-02"},
                        "change_pct": 10,
                    },
                    {
                        "artifact_id": "EV-2",
                        "metric_or_fact": "metric.cac",
                        "value": 1,
                        "time_range": {"start": "2026-02-01", "end": "2026-02-02"},
                        "baseline": 0.5,
                    },
                ]
            }
        )
    )
    assert any(c["type"] == "TIME_RANGE_CONFLICT" for c in conflicts)
    assert not unresolved_blocking(conflicts)


def test_synthesis_includes_conflict_section():
    bb = Blackboard("Mcf")
    mission = SwarmMission(mission_id="Mcf", query="q", time_range={}, intents=set(), initial_lead="performance_agent")
    text = build_claim_aware_response(
        bb,
        mission,
        conflicts=[
            {
                "type": "METHODOLOGY_CONFLICT",
                "blocking": True,
                "resolved": True,
                "accepted_as_limitation": True,
                "description": "mismatch",
                "resolution": {"reason": "Strategy rejected for mechanism mismatch"},
            }
        ],
    )
    assert "Conflicts:" in text
    assert "mechanism mismatch" in text.lower() or "Strategy rejected" in text


def test_conflict_limitations_helper():
    lines = conflict_limitations(
        [
            {
                "type": "MODEL_CONFLICT",
                "accepted_as_limitation": True,
                "resolution": {"reason": "bad model"},
                "description": "x",
            }
        ]
    )
    assert lines and "bad model" in lines[0]
