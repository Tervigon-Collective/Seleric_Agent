"""Skeptic gate — maps PASS/REVISE/REJECT onto claim state and mission status."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from seleric_swarm.coordinator.artifacts.claims import ClaimManager
from seleric_swarm.coordinator.governance.remediation import targeted_remediation_plan

if TYPE_CHECKING:
    from seleric_swarm.runtime import SwarmRuntime


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


async def apply_skeptic_gate(
    *,
    claim_manager: ClaimManager,
    claim_id: str,
    verdict: str,
    followups: list[dict[str, Any]] | None = None,
    mission_id: str,
    remediation_round: int,
    max_remediation_rounds: int = 3,
    prev_followup_signature: str | None = None,
    runtime: SwarmRuntime | None = None,
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
    if verdict in ("REVISE", "REJECT"):
        out["event"] = "skeptic_revise" if verdict == "REVISE" else "skeptic_reject"
        sig = followup_signature(followups)
        out["followup_signature"] = sig
        # Both REVISE and REJECT trigger a remediation round — cap and
        # stall-detect uniformly, otherwise REJECT (which had neither check)
        # can loop indefinitely reproducing the identical rejection.
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
        plan = await targeted_remediation_plan(
            mission_id=mission_id,
            followups=list(followups or []),
            runtime=runtime,
        )
        out["remediation"] = plan
        out["mission_status"] = "remediating"
        if verdict == "REJECT":
            out["open_next_hypothesis"] = True
        return out
    return out
