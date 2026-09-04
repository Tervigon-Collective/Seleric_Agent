"""Unit tests for intake, routing, scoring and the A2A adapter."""

from __future__ import annotations

from seleric_swarm.agents.skeptic import Claim, SkepticDeps
from seleric_swarm.agents.skeptic.a2a import SkepticA2AAdapter
from seleric_swarm.agents.skeptic.agent import SkepticAgent
from seleric_swarm.agents.skeptic.context import SkepticContext
from seleric_swarm.agents.skeptic.intake.claim_classifier import classify_claim
from seleric_swarm.agents.skeptic.intake.claim_parser import parse_claim
from seleric_swarm.agents.skeptic.intake.risk_scorer import score_risk
from seleric_swarm.agents.skeptic.planning.validation_router import select_validators
from seleric_swarm.agents.skeptic.policies import SkepticPolicies
from tests.skeptic.conftest import MISSION, evidence


def _claim(**kw) -> Claim:
    kw.setdefault("mission_id", MISSION)
    kw.setdefault("origin_agent", "x_agent")
    return Claim(**kw)


def test_parser_accepts_legacy_domain_claim():
    legacy = {"claim_id": "CL-9", "claim_type": "causal", "text": "x caused y", "causal_ref": "CAUS-9", "model_ref": "M-1"}
    claim = parse_claim(legacy, mission_id=MISSION)
    assert claim.statement == "x caused y"
    assert claim.causal_refs == ["CAUS-9"]
    assert "CAUS-9" in claim.support_refs and "M-1" in claim.support_refs


def test_classifier_flags_stronger_wording():
    c = _claim(claim_type="numeric", statement="the outage caused the revenue drop")
    result = classify_claim(c)
    assert result.mismatch is True
    assert result.claim_type == "numeric"  # never silently upgraded


def test_risk_class_floor_by_type():
    pol = SkepticPolicies.load()
    low = score_risk(_claim(claim_type="qualitative", statement="things look fine"), policies=pol)
    action = score_risk(_claim(claim_type="action", statement="pause all campaigns"), policies=pol)
    assert low.risk_class in {"R0", "R1"}
    assert action.risk_class == "R5"
    assert 0.0 <= action.score <= 1.0


def test_router_selects_partial_validators():
    pol = SkepticPolicies.load()
    ctx = SkepticContext(claim=_claim(claim_type="forecast", statement="cac will rise"), policies=pol, deps=SkepticDeps())
    ctx.risk_class = "R4"
    assert set(select_validators(ctx)) == {"model", "forecast"}


async def test_a2a_adapter_wraps_verdict():
    agent = SkepticAgent(
        evidence_repo=_repo([evidence("EV-1", "metric.net_sales", 89.0, change_pct=-11.0)]),
    )
    adapter = SkepticA2AAdapter(agent)
    out = await adapter.handle(
        {
            "mission_id": MISSION,
            "intent": "claim_validation",
            "claim": {
                "mission_id": MISSION,
                "claim_type": "numeric",
                "statement": "net sales index was 89",
                "origin_agent": "commerce_agent",
                "support_refs": ["EV-1"],
            },
        }
    )
    assert out["ok"] is True
    assert out["produced"] == "skeptic_verdict"
    assert out["artifact"]["verdict"] in {"PASS", "REVISE", "REJECT"}


def _repo(rows):
    from seleric_swarm.agents.skeptic.registries import InMemoryEvidenceRepository

    return InMemoryEvidenceRepository(rows)
