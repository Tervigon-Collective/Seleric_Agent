"""Synthetic evidence governance — taint propagation and completion status."""

from __future__ import annotations

from typing import Any


def propagate_synthetic(payload: dict[str, Any], *, input_synthetic: bool) -> dict[str, Any]:
    if input_synthetic or payload.get("synthetic"):
        out = dict(payload)
        out["synthetic"] = True
        flags = list(out.get("quality_flags") or [])
        if "SYNTHETIC" not in flags:
            flags.append("SYNTHETIC")
        out["quality_flags"] = flags
        return out
    return payload


def mission_synthetic_status(
    *,
    all_synthetic: bool,
    mixed: bool,
    complete: bool,
) -> str:
    """Map provenance to mission status semantics."""
    if not complete:
        return "partial" if mixed or all_synthetic else "running"
    if all_synthetic:
        return "prototype_completed"
    return "completed"


def fixture_action_language(text: str, *, synthetic: bool) -> str:
    if not synthetic:
        return text
    # Soften imperative action language for fixture outputs
    return (
        text.replace("Recommended actions:", "In this fixture scenario, the recommended modeled action is:")
        .replace("You should", "A modeled action would be to")
        .replace("Must ", "Modeled suggestion: ")
    )
