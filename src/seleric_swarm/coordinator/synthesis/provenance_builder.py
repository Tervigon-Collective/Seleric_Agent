"""Provenance summary for final responses."""

from __future__ import annotations

from typing import Any


def build_provenance_summary(
    *,
    artifact_refs: dict[str, list[str]] | None = None,
    synthetic_summary: dict[str, Any] | None = None,
    claim_refs: list[str] | None = None,
    decomposition_refs: list[str] | None = None,
) -> dict[str, Any]:
    synth = synthetic_summary or {}
    return {
        "artifact_refs": dict(artifact_refs or {}),
        "claim_refs": list(claim_refs or []),
        "decomposition_refs": list(decomposition_refs or []),
        "synthetic": {
            "total": synth.get("total", 0),
            "synthetic": synth.get("synthetic", 0),
            "all_synthetic": bool(synth.get("all_synthetic")),
            "mixed": bool(synth.get("mixed")),
        },
    }
