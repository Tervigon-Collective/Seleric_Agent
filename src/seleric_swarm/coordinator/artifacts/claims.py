"""Managed claim lifecycle: PROPOSED → VALIDATED | CHALLENGED | REJECTED | SUPERSEDED."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from seleric_swarm.coordinator.contracts import ClaimState, ManagedClaim


# Skeptic verdict to claim state mapping
_SKEPTIC_MAP: dict[str, ClaimState] = {
    "PASS": "VALIDATED",
    "REVISE": "CHALLENGED",
    "REJECT": "REJECTED",
}


class ClaimManager:
    """Manages the lifecycle of claims through the skeptic gate.
    
    Claims progress: PROPOSED → (skeptic) → VALIDATED | CHALLENGED | REJECTED
    """

    def __init__(self) -> None:
        self.claims: dict[str, ManagedClaim] = {}

    def propose(
        self,
        *,
        mission_id: str,
        statement: str,
        claim_type: str,
        support_refs: list[str] | None = None,
        origin_agent: str | None = None,
        synthetic: bool = False,
        causal_strength: str | None = None,
    ) -> ManagedClaim:
        """Propose a new claim. Returns the ManagedClaim with PROPOSED state.
        
        Args:
            mission_id: The mission this claim belongs to
            statement: The claim statement text
            claim_type: Type of claim (e.g., "causal", "numeric", "forecast")
            support_refs: List of artifact IDs supporting this claim
            origin_agent: Agent that proposed the claim
            synthetic: Whether the claim is based on synthetic data
            causal_strength: Optional causal strength indicator (e.g., "ASSOCIATION_ONLY", 
                           "PLAUSIBLE_CAUSAL", "CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS", 
                           "STRONGLY_SUPPORTED")
        
        Returns:
            The newly created ManagedClaim instance
        """
        claim_id = f"CL-{uuid4().hex[:8]}"
        claim = ManagedClaim(
            claim_id=claim_id,
            mission_id=mission_id,
            statement=statement,
            claim_type=claim_type,
            state="PROPOSED",
            support_refs=support_refs or [],
            origin_agent=origin_agent,
            synthetic=synthetic,
            causal_strength=causal_strength,
        )
        self.claims[claim_id] = claim
        return claim

    def apply_skeptic_verdict(self, claim_id: str, verdict: str) -> ManagedClaim:
        """Apply skeptic verdict to update claim state.
        
        Args:
            claim_id: The claim to update
            verdict: Skeptic verdict ("PASS", "REVISE", or "REJECT")
        
        Returns:
            The updated ManagedClaim
        """
        if claim_id not in self.claims:
            raise KeyError(f"Claim {claim_id} not found")
        
        claim = self.claims[claim_id]
        new_state = _SKEPTIC_MAP.get(verdict, "PROPOSED")
        claim.state = new_state
        return claim

    def dump(self) -> list[dict[str, Any]]:
        """Export all claims as a list of dictionaries.
        
        Returns:
            List of claim dictionaries suitable for serialization
        """
        return [claim.model_dump() for claim in self.claims.values()]

    def buckets(self) -> dict[str, list[str]]:
        """Partition claims by state into buckets.
        
        Returns:
            Dictionary with keys:
                - claim_refs: all claim IDs
                - validated_claim_refs: VALIDATED claim IDs
                - challenged_claim_refs: CHALLENGED claim IDs
                - rejected_claim_refs: REJECTED claim IDs
        """
        all_refs = list(self.claims.keys())
        validated = [cid for cid, c in self.claims.items() if c.state == "VALIDATED"]
        challenged = [cid for cid, c in self.claims.items() if c.state == "CHALLENGED"]
        rejected = [cid for cid, c in self.claims.items() if c.state == "REJECTED"]
        
        return {
            "claim_refs": all_refs,
            "validated_claim_refs": validated,
            "challenged_claim_refs": challenged,
            "rejected_claim_refs": rejected,
        }
