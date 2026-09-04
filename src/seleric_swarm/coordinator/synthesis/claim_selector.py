"""Final claim selector — only allowed claims enter synthesis."""

from __future__ import annotations

from typing import Any

ALLOWED_IN_SYNTHESIS = {"PROPOSED", "SUPPORTED", "CHALLENGED", "VALIDATED"}
OMIT = {"REJECTED", "SUPERSEDED"}


def select_allowed_claims(managed_claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in managed_claims if c.get("state") not in OMIT]


def claim_language_tier(claim: dict[str, Any]) -> str:
    """Map claim state to synthesis language tier."""
    state = claim.get("state")
    if state == "VALIDATED":
        return "strong"
    if state == "SUPPORTED":
        return "evidence_supports"
    if state == "CHALLENGED":
        return "leading_unresolved"
    if state == "PROPOSED":
        return "proposed"
    return "omit"


def partition_claims(managed_claims: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {
        "validated": [],
        "supported": [],
        "challenged": [],
        "proposed": [],
        "rejected": [],
        "superseded": [],
    }
    for c in managed_claims:
        state = str(c.get("state") or "")
        key = state.lower()
        if key in buckets:
            buckets[key].append(c)
    return buckets
