"""Evidence-gap aggregation + expected-information-gain ranking (spec sec. 34, 50).

Validators emit gaps locally; this module de-duplicates them, adds any
cross-cutting gaps the validators could not see individually, and ranks by a
simple deterministic proxy for::

    Priority ~ ExpectedInformationGain * ClaimImpact / EstimatedCost
"""

from __future__ import annotations

from seleric_swarm.agents.skeptic.context import SkepticContext, ValidatorOutcome
from seleric_swarm.agents.skeptic.contracts import EvidenceGap

_COST = {  # rough capability cost buckets
    "metric_observation": 1.0,
    "device_segmentation": 1.0,
    "cross_source_reconciliation": 2.0,
    "metric_definition_reconciliation": 2.0,
    "causal_diagnosis": 4.0,
    "causal_refutation": 3.0,
    "forecasting": 4.0,
    "model_registry_lookup": 1.0,
    "stock_cover_analysis": 1.5,
}


def collect_gaps(ctx: SkepticContext, outcomes: list[ValidatorOutcome]) -> list[EvidenceGap]:
    gaps: list[EvidenceGap] = []
    seen: set[str] = set()
    for oc in outcomes:
        for g in oc.evidence_gaps:
            key = g.description.strip().lower()
            if key not in seen:
                seen.add(key)
                gaps.append(g)

    # cross-cutting: causal/correlation claim with no control segment mentioned
    if ctx.claim.claim_type in {"causal", "correlation"}:
        has_control = any(
            "control" in (e.dimensions.get("segment", "") or "").lower()
            or e.dimensions.get("device") == "desktop"
            for e in ctx.all_evidence()
        ) or bool(ctx.claim.metadata.get("control_segment_checked"))
        if not has_control and "no control segment" not in seen:
            gaps.append(
                EvidenceGap(
                    description="No unaffected control segment was evaluated.",
                    reason_required="A control rules out a common shock affecting all segments.",
                    capability_required="device_segmentation",
                    blocking=False,
                    priority=7,
                )
            )

    impact = float(ctx.risk_components.get("claim_impact", 0.4) or 0.4)
    for g in gaps:
        eig = 0.9 if g.blocking else 0.5
        cost = _COST.get(g.capability_required or "", 2.0)
        g.priority = max(1, min(10, round((eig * (0.5 + impact) / cost) * 10)))
    return sorted(gaps, key=lambda g: (-int(g.blocking), -g.priority))
