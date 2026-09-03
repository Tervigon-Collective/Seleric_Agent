"""Prototype synthesizer (architecture sec. 45).

Assembles the verified-intelligence narrative from Blackboard artifacts. No LLM.
Because every input is fixture/template-derived, the output carries a prominent
SYNTHETIC banner and the claim summary is labelled ``trust_label: SYNTHETIC``
rather than ``VERIFIED`` - the swarm never emits a fake business conclusion as
fact.
"""

from __future__ import annotations

from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.mission import SwarmMission

_BANNER = (
    "PROTOTYPE OUTPUT - all evidence below is SYNTHETIC (fixture/template providers). "
    "Do not act on these numbers. Wire real MCP data and models, then re-run."
)


def _fmt_pct(x: object) -> str:
    try:
        return f"{float(x):+.1f}%"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(x)


def build_response(blackboard: Blackboard, mission: SwarmMission) -> str:
    lines: list[str] = [_BANNER, "", f"Question: {mission.query}", ""]

    anomalies = blackboard.by_type("anomaly")
    retained = [h for h in blackboard.by_type("hypothesis") if h.get("status") == "retained"]
    causal = blackboard.by_type("causal")
    predictions = blackboard.by_type("prediction")
    strategies = blackboard.by_type("strategy")
    skeptic = blackboard.by_type("skeptic")

    lines.append("Leadership path:")
    path = [mission.initial_lead] + [h.get("to_agent") for h in blackboard.handoff_history]
    lines.append("  " + " -> ".join(str(p) for p in path))
    for h in blackboard.handoff_history:
        lines.append(f"  transfer: {h.get('from_agent')} -> {h.get('to_agent')} | {h.get('reason')}")
    lines.append("")

    if retained:
        lines.append("Root cause (retained hypothesis):")
        lines.append(f"  {retained[0]['statement']}")
        if causal and causal[0].get("passed"):
            c = causal[0]
            lines.append(
                f"  causal check: {c['treatment']} -> {c['outcome']} effect {c.get('effect')} "
                f"CI {c.get('effect_ci')} | refutations passed"
            )
        lines.append("")

    if anomalies:
        lines.append("Key anomalies:")
        for a in sorted(anomalies, key=lambda x: abs(x.get("deviation_pct") or 0), reverse=True)[:5]:
            dims = a.get("dimensions") or {}
            tag = f" [{','.join(f'{k}={v}' for k, v in dims.items())}]" if dims else ""
            lines.append(f"  {a['metric_id']}{tag}: {_fmt_pct(a.get('deviation_pct'))} ({a.get('direction')})")
        lines.append("")

    if predictions:
        p = predictions[0]
        lines.append("Projection if unchanged:")
        lines.append(f"  {p['target']} ~ {p.get('prediction')} over {p.get('horizon')} (interval {p.get('interval')})")
        if p.get("secondary"):
            s = p["secondary"]
            lines.append(f"  {s.get('target')} ~ {s.get('prediction')} (interval {s.get('interval')})")
        lines.append("")

    if strategies:
        s = strategies[0]
        lines.append("Recommended actions:")
        for rec in s.get("recommended", []):
            lines.append(f"  - {rec}")
        lines.append(f"  rationale: {s.get('rationale')}")
        lines.append("")

    if skeptic:
        k = skeptic[0]
        lines.append(f"Skeptic verdict: {k.get('verdict')}")
        for prob in k.get("problems", []):
            lines.append(f"  - {prob.get('type')}: {prob.get('description')}")
        lines.append("")

    lines.append("Claim summary (trust_label: SYNTHETIC - not verified):")
    if retained:
        lines.append(f"  - {retained[0]['statement']} [SYNTHETIC]")
    for p in predictions:
        lines.append(f"  - {p['target']} projected {p.get('prediction')} in {p.get('horizon')} [SYNTHETIC]")

    return "\n".join(lines).rstrip()
