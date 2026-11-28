"""Diagnosis must explain gated hits only — never invent numbers or recommend unlisted actions.

B31: diagnose is gates-only (pending_bot); the Marketing bot fills cause/bullets later.
"""

from __future__ import annotations

import asyncio

from seleric_swarm.services.live_monitor import diagnose as D


def _finding(*, actions=None, evidence=None, confidence=0.91) -> dict:
    return {
        "finding_id": "f1",
        "brand_id": 20,
        "title": "ROAS below target / break-even",
        "severity": "warning",
        "summary": "3-day ROAS 1.80 is below the target 2.90.",
        "confidence": confidence,
        "diagnosis": {
            "title": "ROAS below target / break-even",
            "entity": {"level": "campaign", "id": "c1", "name": "SnugSuit", "path": ["TH", "SnugSuit"]},
            "evidence": evidence or {
                "spend_3d": 20000, "roas_3d": 1.8, "target_roas": 2.9, "be_roas": 2.2,
                "p_below_target": 0.91, "min_spend_ok": True,
            },
            "actions": actions or [
                {"id": "budget_down", "title": "Lower budget", "risk": "spend"},
                {"id": "pause", "title": "Pause", "risk": "delivery"},
                {"id": "open_agent", "title": "Open in Marketing Agent", "risk": "link"},
            ],
        },
    }


def test_gates_and_artifacts_are_deterministic_from_evidence():
    finding = _finding()
    evidence = finding["diagnosis"]["evidence"]
    gates = D.gates_from_evidence(evidence, finding["confidence"])
    assert gates["confidence"] == 0.91
    assert gates["min_volume_ok"] is True
    assert gates["learning"] is False
    assert gates["window"] == "3d"
    arts = D.artifacts_from_evidence(evidence, finding["summary"])
    assert arts[0]["kind"] == "metric_row"
    keys = {r["key"] for r in arts[0]["payload"]["rows"]}
    assert "spend_3d" in keys and "roas_3d" in keys
    assert "made_up_roas" not in keys


def test_pick_recommended_stays_in_allow_list():
    actions = [
        {"id": "open_agent", "title": "Open in Marketing Agent", "risk": "link"},
        {"id": "pause", "title": "Pause", "risk": "delivery"},
    ]
    rid, title = D.pick_recommended(actions)
    assert rid == "pause" and title == "Pause"
    rid, _ = D.pick_recommended(actions, preferred="budget_down")
    assert rid == "pause"  # not allow-listed → fall back
    rid, _ = D.pick_recommended(actions, preferred="pause")
    assert rid == "pause"
    rid, _ = D.pick_recommended([{"id": "open_agent", "title": "Open", "risk": "link"}])
    assert rid == "open_agent"


def test_diagnose_gates_only_pending_bot_no_llm():
    """B31: even with an llm stub, diagnose never calls it and leaves pending_bot."""

    class Boom:
        async def complete(self, *_a, **_k):
            raise AssertionError("diagnose must not call the LLM")

    out = asyncio.run(
        D.diagnose(Boom(), "gpt-test", _finding(),
                   {"be_roas_platform": 2.2, "target_roas": 2.9, "allowable_cpa": 1200},
                   change="opened")
    )
    assert out["status"] == "pending_bot"
    assert out["diagnosis_source"] == "awaiting_bot"
    assert out["cause"] == D.PENDING_CAUSE
    assert out["bullets"] == []
    assert out["recommended_action_id"] == "budget_down"
    assert out["gates"]["confidence"] == 0.91
    assert out["artifacts"][0]["source"] == "rule.evidence"


def test_confidence_boundary_uses_hit_confidence():
    gates = D.gates_from_evidence({"p_below_target": 0.8, "spend_3d": 5000}, 0.8)
    assert gates["confidence"] == 0.8
    learning = D.gates_from_evidence({"learning": True, "spend_today": 4000}, 0.95)
    assert learning["learning"] is True
