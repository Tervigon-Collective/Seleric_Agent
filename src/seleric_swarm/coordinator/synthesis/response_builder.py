"""Claim-aware response synthesis — never call CHALLENGED claims validated."""

from __future__ import annotations

import re
from typing import Any

from seleric_swarm.coordinator.policies import CoordinatorPolicies, load_coordinator_policies
from seleric_swarm.coordinator.synthesis.claim_selector import select_allowed_claims
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.mission import SwarmMission

_CAUSAL_LANGUAGE = {
    "ASSOCIATION_ONLY": "associated with",
    "PLAUSIBLE_CAUSAL": "may be contributing",
    "CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS": (
        "evidence supports as a causal contributor under stated assumptions"
    ),
    "STRONGLY_SUPPORTED": "strong evidence indicates a primary contributor",
}

def _sanitize(text: str, *, challenged: bool, policies: CoordinatorPolicies) -> str:
    if not challenged:
        return text
    out = text
    for phrase in policies.synthesis.forbidden_phrases_when_challenged:
        out = re.sub(re.escape(phrase), "challenged hypothesis", out, flags=re.IGNORECASE)
    # Strip affirmative validation language only — never rewrite "not validated".
    for phrase in ("confirmed root cause", "proven cause", "established root cause"):
        out = re.sub(rf"\b{re.escape(phrase)}\b", "challenged hypothesis", out, flags=re.IGNORECASE)
    out = re.sub(r"(?<!\bnot )\bvalidated\b", "challenged", out, flags=re.IGNORECASE)
    return out


def build_claim_aware_response(
    blackboard: Blackboard,
    mission: SwarmMission,
    *,
    managed_claims: list[dict[str, Any]] | None = None,
    completion_status: str | None = None,
    policies: CoordinatorPolicies | None = None,
    conflicts: list[dict[str, Any]] | None = None,
    extra_limitations: list[str] | None = None,
) -> str:
    policies = policies or load_coordinator_policies()
    claims = select_allowed_claims(list(managed_claims or []))
    challenged = [c for c in claims if c.get("state") == "CHALLENGED"]
    validated = [c for c in claims if c.get("state") == "VALIDATED"]
    supported = [c for c in claims if c.get("state") == "SUPPORTED"]

    summary = blackboard.synthetic_summary()
    lines: list[str] = []

    if summary.get("all_synthetic") or completion_status == "prototype_completed":
        # Keep "PROTOTYPE OUTPUT" for swarm_v1 regression compatibility.
        lines += [
            (
                "PROTOTYPE OUTPUT - every artifact below is SYNTHETIC (fixture/template providers). "
                "Do not act on these numbers. Wire real MCP data and models, then re-run."
            ),
            policies.synthesis.prototype_banner.strip(),
            "",
        ]
    elif summary.get("mixed"):
        lines += [
            f"MIXED PROVENANCE - {summary['synthetic']}/{summary['total']} artifacts are SYNTHETIC.",
            "",
        ]

    lines += [f"Question: {mission.query}", ""]

    lines.append("Leadership path:")
    path = [mission.initial_lead] + [h.get("to_agent") for h in blackboard.handoff_history]
    lines.append("  " + " -> ".join(str(p) for p in path))
    for h in blackboard.handoff_history:
        lines.append(f"  transfer: {h.get('from_agent')} -> {h.get('to_agent')} | {h.get('reason')}")
    lines.append("")

    # Primary finding — wording depends on claim state
    retained = [h for h in blackboard.by_type("hypothesis") if h.get("status") == "retained"]
    if validated:
        c = validated[0]
        strength = c.get("causal_strength") or "STRONGLY_SUPPORTED"
        verb = _CAUSAL_LANGUAGE.get(strength, "evidence supports")
        lines.append("Primary finding:")
        lines.append(f"  {verb}: {c.get('statement')}")
        lines.append("  claim_state: VALIDATED")
        lines.append("")
    elif challenged:
        c = challenged[0]
        lines.append("Leading (unresolved) hypothesis:")
        lines.append(f"  CHALLENGED: {c.get('statement')}")
        lines.append("  This claim is not validated; remediation is required.")
        lines.append("")
    elif supported:
        c = supported[0]
        lines.append("Evidence-supported hypothesis:")
        lines.append(f"  {c.get('statement')}")
        lines.append("  claim_state: SUPPORTED (pending Skeptic)")
        lines.append("")
    elif retained:
        # No managed claim yet — do NOT call it root cause if skeptic != PASS
        skeptic = (blackboard.by_type("skeptic") or [{}])[-1]
        verdict = skeptic.get("verdict")
        if verdict == "PASS":
            lines.append("Primary finding:")
            lines.append(f"  {retained[0]['statement']}")
        else:
            lines.append("Leading (unresolved) hypothesis:")
            lines.append(f"  CHALLENGED: {retained[0]['statement']}")
            if verdict == "REVISE":
                lines.append("  Skeptic verdict: REVISE — conclusion is not validated.")
        lines.append("")

    anomalies = blackboard.by_type("anomaly")
    if anomalies:
        lines.append("Key anomalies:")
        for a in sorted(anomalies, key=lambda x: abs(x.get("deviation_pct") or 0), reverse=True)[:5]:
            dims = a.get("dimensions") or {}
            tag = f" [{','.join(f'{k}={v}' for k, v in dims.items())}]" if dims else ""
            try:
                pct = f"{float(a.get('deviation_pct')):+.1f}%"
            except (TypeError, ValueError):
                pct = str(a.get("deviation_pct"))
            lines.append(f"  {a.get('metric_id')}{tag}: {pct} ({a.get('direction')})")
        lines.append("")

    predictions = blackboard.by_type("prediction")
    if predictions:
        p = predictions[0]
        lines.append("Projection if unchanged:")
        lines.append(
            f"  {p.get('target')} ~ {p.get('prediction')} over {p.get('horizon')} "
            f"(interval {p.get('interval')}; model {p.get('model')})"
        )
        lines.append("")

    strategies = blackboard.by_type("strategy")
    if strategies and not challenged:
        s = strategies[0]
        if summary.get("all_synthetic"):
            lines.append("In this fixture scenario, the recommended modeled action is:")
        else:
            lines.append("Recommended actions:")
        for rec in s.get("recommended") or []:
            lines.append(f"  - {rec}")
        rationale = str(s.get("rationale") or "")
        if not validated:
            rationale = re.sub(
                r"\bvalidated mechanism\b",
                "leading hypothesis",
                rationale,
                flags=re.IGNORECASE,
            )
        lines.append(f"  rationale: {rationale}")
        lines.append("")
    elif strategies and challenged:
        lines.append("Actions deferred: primary claim remains CHALLENGED.")
        lines.append("")

    skeptic = blackboard.by_type("skeptic")
    if skeptic:
        k = skeptic[-1]
        lines.append(f"Skeptic verdict: {k.get('verdict')}")
        if k.get("required_followups"):
            lines.append("  required follow-ups:")
            for f in k["required_followups"][:5]:
                lines.append(f"    - {f.get('question') or f.get('objective') or f}")
        lines.append("")

    if conflicts:
        unresolved = [c for c in conflicts if c.get("blocking") and not c.get("resolved") and not c.get("accepted_as_limitation")]
        noted = [c for c in conflicts if c.get("accepted_as_limitation") or (c.get("resolved") and c.get("resolution"))]
        if unresolved or noted:
            lines.append("Conflicts:")
            for c in unresolved[:5]:
                lines.append(f"  UNRESOLVED [{c.get('type')}]: {c.get('description')}")
            for c in noted[:5]:
                res = c.get("resolution") or {}
                lines.append(
                    f"  handled [{c.get('type')}]: {res.get('reason') or c.get('description')}"
                )
            lines.append("")

    if extra_limitations:
        lines.append("Limitations:")
        for lim in extra_limitations[:8]:
            lines.append(f"  - {lim}")
        lines.append("")

    text = "\n".join(lines)
    return _sanitize(text, challenged=bool(challenged) or (
        (blackboard.by_type("skeptic") or [{}])[-1].get("verdict") == "REVISE"
    ), policies=policies)
