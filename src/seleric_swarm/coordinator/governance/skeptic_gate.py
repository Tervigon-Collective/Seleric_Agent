"""Skeptic gate — maps PASS/REVISE/REJECT onto claim state and mission status."""

from __future__ import annotations

import hashlib
from typing import Any

from seleric_swarm.coordinator.artifacts.claims import ClaimManager
from seleric_swarm.coordinator.governance.remediation import targeted_remediation_plan


def followup_signature(followups: list[dict[str, Any]] | None) -> str:
    """Stable fingerprint of a Skeptic's follow-up set. Two consecutive rounds
    with the same signature mean remediation is not adding information -> stop."""

    keys = sorted(
        str(
            f.get("question")
            or f.get("objective")
            or f.get("requested_capability")
            or f.get("capability")
            or f
        )
        for f in (followups or [])
    )
    return hashlib.sha1("|".join(keys).encode()).hexdigest()[:16]


def apply_skeptic_gate(
    *,
    claim_manager: ClaimManager,
    claim_id: str,
    verdict: str,
    followups: list[dict[str, Any]] | None = None,
    mission_id: str,
    remediation_round: int,
    max_remediation_rounds: int = 3,
    prev_followup_signature: str | None = None,
) -> dict[str, Any]:
    claim = claim_manager.apply_skeptic_verdict(claim_id, verdict)
    buckets = claim_manager.buckets()
    out: dict[str, Any] = {
        "verdict": verdict,
        "claim": claim.model_dump(),
        **buckets,
        "remediation": None,
        "mission_status": None,
        "event": None,
    }
    if verdict == "PASS":
        out["mission_status"] = "validating"
        out["event"] = "skeptic_pass"
        return out
    if verdict == "REVISE":
        out["event"] = "skeptic_revise"
        sig = followup_signature(followups)
        out["followup_signature"] = sig
        if remediation_round >= max_remediation_rounds:
            out["mission_status"] = "partial"
            out["status_reason"] = "max_remediation_rounds_exhausted"
            return out
        # Stall guard: an earlier round asked for the exact same follow-ups.
        # Re-running with no new information will not change the verdict.
        if prev_followup_signature is not None and sig == prev_followup_signature and remediation_round >= 1:
            out["mission_status"] = "partial"
            out["status_reason"] = "remediation_stalled_no_new_information"
            return out
        plan = targeted_remediation_plan(
            mission_id=mission_id,
            followups=list(followups or []),
        )
        out["remediation"] = plan
        out["mission_status"] = "remediating"
        return out
    if verdict == "REJECT":
        out["event"] = "skeptic_reject"
        plan = targeted_remediation_plan(
            mission_id=mission_id,
            followups=list(followups or []),
        )
        out["remediation"] = plan
        out["mission_status"] = "remediating"
        out["open_next_hypothesis"] = True
        return out
    return out
