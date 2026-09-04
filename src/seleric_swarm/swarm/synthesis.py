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

_BANNER_ALL = (
    "PROTOTYPE OUTPUT - every artifact below is SYNTHETIC (fixture/template providers). "
    "Do not act on these numbers. Wire real MCP data and models, then re-run."
)
_BANNER_MIXED = (
    "MIXED PROVENANCE - {synthetic}/{total} artifacts are SYNTHETIC (fixture/template). "
    "Treat any claim resting on a synthetic artifact as unverified."
)


def _banner(summary: dict[str, object]) -> str | None:
    if summary.get("all_synthetic"):
        return _BANNER_ALL
    if summary.get("mixed"):
        return _BANNER_MIXED.format(synthetic=summary["synthetic"], total=summary["total"])
    return None  # fully real run: no banner


def _fmt_pct(x: object) -> str:
    try:
        return f"{float(x):+.1f}%"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(x)


def build_response(blackboard: Blackboard, mission: SwarmMission) -> str:
    summary = blackboard.synthetic_summary()
    lines: list[str] = []
    banner = _banner(summary)
    if banner:
        lines += [banner, ""]
    lines += [f"Question: {mission.query}", ""]

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
        # Claim-safe wording: never assert "Root cause" — claim-aware synthesis owns
        # validated vs leading language. This legacy helper is a thin fallback only.
        lines.append("Leading hypothesis (legacy synthesizer — prefer claim-aware builder):")
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
        k = skeptic[-1]  # the latest verdict (a re-check appends a second artifact)
        lines.append(f"Skeptic verdict: {k.get('verdict')}")
        for prob in k.get("problems", []):
            lines.append(f"  - {prob.get('type')}: {prob.get('description')}")
        lines.append("")

    lines.append("Claim summary (trust_label per claim):")
    for claim_text, art in _claims(retained, predictions):
        label = "SYNTHETIC" if art.get("synthetic") else "VERIFIED"
        lines.append(f"  - {claim_text} [{label}]")

    return "\n".join(lines).rstrip()


def _claims(retained: list[dict], predictions: list[dict]) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    if retained:
        out.append((retained[0]["statement"], retained[0]))
    for p in predictions:
        out.append((f"{p['target']} projected {p.get('prediction')} in {p.get('horizon')}", p))
    return out
