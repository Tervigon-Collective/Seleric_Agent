"""Claim-aware synthesis."""

from seleric_swarm.coordinator.synthesis.claim_selector import (
    claim_language_tier,
    partition_claims,
    select_allowed_claims,
)
from seleric_swarm.coordinator.synthesis.provenance_builder import build_provenance_summary
from seleric_swarm.coordinator.synthesis.response_builder import build_claim_aware_response

__all__ = [
    "build_claim_aware_response",
    "build_provenance_summary",
    "claim_language_tier",
    "partition_claims",
    "select_allowed_claims",
]
