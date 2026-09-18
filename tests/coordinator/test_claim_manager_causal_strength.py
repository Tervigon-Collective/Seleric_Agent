"""Test ClaimManager handles causal_strength parameter correctly.

Regression test for TypeError: propose() got an unexpected keyword argument 'causal_strength'
"""

from __future__ import annotations

import pytest

from seleric_swarm.coordinator.artifacts.claims import ClaimManager
from seleric_swarm.coordinator.governance.skeptic_gate import apply_skeptic_gate


def test_propose_accepts_causal_strength():
    """ClaimManager.propose() should accept causal_strength kwarg."""
    mgr = ClaimManager()
    
    # Test with causal_strength parameter
    claim = mgr.propose(
        mission_id="M_test",
        statement="Frontend regression caused CVR drop",
        claim_type="causal",
        causal_strength="PLAUSIBLE_CAUSAL",
    )
    
    assert claim.claim_id is not None
    assert claim.causal_strength == "PLAUSIBLE_CAUSAL"
    assert claim.state == "PROPOSED"


def test_propose_causal_strength_variations():
    """Test different causal_strength values are preserved correctly."""
    mgr = ClaimManager()
    
    strength_values = [
        "ASSOCIATION",
        "PLAUSIBLE_CAUSAL",
        "CAUSALLY_SUPPORTED",
        "STRONGLY_SUPPORTED",
        None,  # No strength provided
    ]
    
    for strength in strength_values:
        claim = mgr.propose(
            mission_id="M_test",
            statement=f"Test claim with strength {strength}",
            claim_type="causal",
            causal_strength=strength,
        )
        
        assert claim.causal_strength == strength


def test_propose_with_all_parameters():
    """Test propose with all parameters including causal_strength."""
    mgr = ClaimManager()
    
    claim = mgr.propose(
        mission_id="M_full",
        statement="Complete test claim",
        claim_type="causal",
        support_refs=["EV-1", "EV-2"],
        origin_agent="diagnostic_agent",
        synthetic=True,
        causal_strength="STRONGLY_SUPPORTED",
    )
    
    assert claim.mission_id == "M_full"
    assert claim.statement == "Complete test claim"
    assert claim.claim_type == "causal"
    assert claim.support_refs == ["EV-1", "EV-2"]
    assert claim.origin_agent == "diagnostic_agent"
    assert claim.synthetic is True
    assert claim.causal_strength == "STRONGLY_SUPPORTED"


@pytest.mark.asyncio
async def test_skeptic_gate_with_causal_strength():
    """Test that skeptic gate works with claims that have causal_strength."""
    mgr = ClaimManager()
    
    claim = mgr.propose(
        mission_id="M_skeptic",
        statement="Causal claim with strength",
        claim_type="causal",
        causal_strength="CAUSALLY_SUPPORTED",
    )
    
    # Apply PASS verdict
    gate = await apply_skeptic_gate(
        claim_manager=mgr,
        claim_id=claim.claim_id,
        verdict="PASS",
        followups=[],
        mission_id="M_skeptic",
        remediation_round=0,
    )
    
    # Claim should be validated
    assert mgr.claims[claim.claim_id].state == "VALIDATED"
    assert claim.claim_id in gate["validated_claim_refs"]
    
    # causal_strength should be preserved
    validated_claim = mgr.claims[claim.claim_id]
    assert validated_claim.causal_strength == "CAUSALLY_SUPPORTED"


def test_dump_preserves_causal_strength():
    """Test that dump() preserves causal_strength in output."""
    mgr = ClaimManager()
    
    mgr.propose(
        mission_id="M_dump",
        statement="Test claim",
        claim_type="causal",
        causal_strength="PLAUSIBLE_CAUSAL",
    )
    
    dumped = mgr.dump()
    assert len(dumped) == 1
    assert dumped[0]["causal_strength"] == "PLAUSIBLE_CAUSAL"


def test_propose_without_causal_strength_is_none():
    """Test that omitting causal_strength results in None (not an error)."""
    mgr = ClaimManager()
    
    # Should work without causal_strength parameter
    claim = mgr.propose(
        mission_id="M_no_strength",
        statement="Claim without strength",
        claim_type="numeric",
    )
    
    assert claim.causal_strength is None
