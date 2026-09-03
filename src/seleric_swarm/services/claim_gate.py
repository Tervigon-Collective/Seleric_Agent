from __future__ import annotations

from typing import Any


def validate_claim(claim: dict[str, Any]) -> tuple[bool, list[str]]:
    problems: list[str] = []
    claim_type = claim.get("claim_type")
    support = claim.get("support_refs") or []

    if claim_type in {"numeric", "causal", "forecast", "recommendation"} and not support:
        problems.append("material claim has no support references")

    if claim_type == "forecast" and not claim.get("model_ref"):
        problems.append("forecast claim has no model reference")

    if claim_type == "causal" and not claim.get("causal_ref"):
        problems.append("causal claim has no causal analysis reference")

    return (not problems, problems)
