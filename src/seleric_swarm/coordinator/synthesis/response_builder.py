"""Claim-aware response synthesis — never call CHALLENGED claims validated."""

from __future__ import annotations

import re
from typing import Any

from seleric_swarm.coordinator.policies import CoordinatorPolicies, load_coordinator_policies
from seleric_swarm.coordinator.synthesis.claim_selector import select_allowed_claims
from seleric_swarm.services.metrics import MetricRegistry
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.mission import SwarmMission

_GAP_RE = re.compile(r"^([A-Za-z0-9_.]+):\s*no data for")


def gapped_metric_ids(limitations: list[str] | None, metrics: MetricRegistry | None) -> set[str]:
    """Metric ids reported as having no data for the mission window
    (``McpFetchStats.limitations()`` lines), canonicalized so "X" and
    "metric.X" collapse to the same id. An anomaly for one of these must
    never be rendered as a confident % — the number has nothing behind it.
    """
    ids: set[str] = set()
    for line in limitations or []:
        m = _GAP_RE.match(line.strip())
        if not m:
            continue
        raw = m.group(1)
        ids.add(metrics.canonical_id(raw) if metrics is not None else raw)
    return ids


def clean_anomalies(
    anomalies: list[dict[str, Any]],
    *,
    limitations: list[str] | None = None,
    metrics: MetricRegistry | None = None,
) -> list[dict[str, Any]]:
    """Anomalies safe to state as fact: deduped by canonical metric id (a
    bare id and its "metric."-prefixed alias must not both surface as
    separate findings) and stripped of anything whose metric has a reported
    data gap for this window.

    # ponytail: matches gaps by exact metric id only; a derived/composite
    # metric (e.g. a rate) whose *component* events are gapped but which
    # itself returned data still passes through. Add a metric dependency
    # graph if that undercatches in practice.
    """
    gapped = gapped_metric_ids(limitations, metrics)
    best: dict[tuple[str, tuple[tuple[Any, Any], ...]], dict[str, Any]] = {}
    for a in anomalies:
        mid = a.get("metric_id")
        if not mid:
            continue
        canon = metrics.canonical_id(mid) if metrics is not None else str(mid)
        if canon in gapped:
            continue
        dims_key = tuple(sorted((a.get("dimensions") or {}).items()))
        key = (canon, dims_key)
        existing = best.get(key)
        if existing is None or abs(a.get("deviation_pct") or 0) > abs(existing.get("deviation_pct") or 0):
            best[key] = a
    return list(best.values())


def comparisons_for_answer(blackboard: Blackboard) -> list[dict[str, Any]]:
    """Period A / period B / delta for a comparison-intent question.

    Shared by both the deterministic template below and the LLM synthesizer
    (coordinator/synthesis/llm_response.py) — neither used to have any
    channel for this: comparison evidence is posted as plain Evidence
    artifacts (swarm/specialists/observer.py's comparison branch), never as
    anomalies, so a "compare June to July" question had nothing to answer
    from and both paths fell back to "no separate figures for those periods."
    """
    evidence = blackboard.by_type("evidence")
    by_id = {e["artifact_id"]: e for e in evidence}
    out: list[dict[str, Any]] = []
    for e in evidence:
        metric = str(e.get("metric_or_fact") or "")
        if not metric.endswith(".delta"):
            continue
        prov = e.get("provenance") or {}
        row_a = by_id.get(prov.get("period_a_evidence_id"))
        row_b = by_id.get(prov.get("period_b_evidence_id"))
        if row_a is None or row_b is None:
            continue
        out.append(
            {
                "metric_id": metric.removesuffix(".delta"),
                "period_a": {"value": row_a.get("value"), "time_range": row_a.get("time_range")},
                "period_b": {"value": row_b.get("value"), "time_range": row_b.get("time_range")},
                "delta": e.get("value"),
            }
        )
    return out


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
    metrics: MetricRegistry | None = None,
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

    if completion_status not in (None, "completed", "prototype_completed"):
        # Mission ran out of budget/rounds without satisfying its objectives —
        # the anomalies/claims below are raw signals, not a confirmed
        # diagnosis. Independent of the synthetic/mixed banners above (a
        # mission can be all-real-data AND still incomplete).
        lines += [
            (
                f"MISSION {str(completion_status).upper()} - not all objectives were satisfied this run; "
                "treat the findings below as raw signals, not a confirmed diagnosis."
            ),
            "",
        ]

    lines += [f"Question: {mission.query}", ""]

    # Directional premise verification (e.g. asking why CAC increased when data shows CAC dropped)
    q_lower = mission.query.lower()
    up = any(w in q_lower for w in ("increase", "increas", "rise", "risen", "rising", "grew", "growing", "growth", "higher", "up", "spike", "surge"))
    down = any(w in q_lower for w in ("decrease", "decreas", "drop", "dropped", "fell", "fallen", "falling", "declin", "lower", "down", "dip", "plunge"))
    implied_dir = "up" if (up and not down) else ("down" if (down and not up) else None)
    if implied_dir:
        for a in blackboard.by_type("anomaly"):
            actual_dir = a.get("direction")
            mid = a.get("metric_id") or ""
            if actual_dir in {"up", "down"} and actual_dir != implied_dir:
                dev = a.get("deviation_pct")
                pct_str = f" ({float(dev):+.1f}%)" if isinstance(dev, (int, float)) else ""
                lines += [
                    (
                        f"[Premise Notice]: The question asks why {mid or 'the metric'} went {implied_dir}, but observed "
                        f"data for this period shows it actually went {actual_dir}{pct_str}."
                    ),
                    "",
                ]
                break

    # Primary finding — wording depends on claim state
    retained = [h for h in blackboard.by_type("hypothesis") if h.get("status") == "retained"]
    if validated:
        c = validated[0]
        # Unset means the confidence tier genuinely wasn't computed — the safe
        # ceiling is the weakest tier, never the strongest (see swarm/artifacts.py Causal.confidence).
        strength = c.get("causal_strength") or "ASSOCIATION_ONLY"
        verb = _CAUSAL_LANGUAGE.get(strength, "evidence supports")
        lines.append("Primary finding:")
        lines.append(f"  {verb}: {c.get('statement')}")
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
        lines.append("  This finding is evidence-supported but not yet independently verified.")
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
                lines.append("  This conclusion needs revision and is not validated.")
        if len(retained) > 1:
            lines.append("Contributing causal drivers (drill-down):")
            for h in retained[1:]:
                lines.append(f"  - {h.get('statement')}")
        lines.append("")

    comparisons = comparisons_for_answer(blackboard)
    if comparisons:
        lines.append("Comparison:")
        for c in comparisons:
            a, b = c["period_a"], c["period_b"]
            lines.append(
                f"  {c['metric_id']}: period A ({a['time_range'].get('start')} to {a['time_range'].get('end')}) "
                f"= {a['value']}; period B ({b['time_range'].get('start')} to {b['time_range'].get('end')}) "
                f"= {b['value']}; delta (A-B) = {c['delta']}"
            )
        lines.append("")

    anomalies = clean_anomalies(
        blackboard.by_type("anomaly"), limitations=extra_limitations, metrics=metrics
    )
    if anomalies:
        lines.append("Key anomalies:")
        for a in sorted(anomalies, key=lambda x: abs(x.get("deviation_pct") or 0), reverse=True)[:5]:
            dims = a.get("dimensions") or {}
            tag = f" [{','.join(f'{k}={v}' for k, v in dims.items())}]" if dims else ""
            _dev = a.get("deviation_pct")
            pct = f"{float(_dev):+.1f}%" if isinstance(_dev, (int, float)) else str(_dev)
            mid_name = str(a.get('metric_id') or '').removeprefix("metric.").replace("_", " ").title()
            lines.append(f"  {mid_name}{tag}: {pct} ({a.get('direction')})")
        lines.append("")

    predictions = blackboard.by_type("prediction")
    if predictions:
        p = predictions[0]
        model_info = p.get("model")
        model_name = model_info.get("id") if isinstance(model_info, dict) else str(model_info or "")
        target_name = str(p.get("target") or "").removeprefix("metric.").replace("_", " ").title()
        _pred_val = p.get("prediction")
        pred_val = f"{float(_pred_val):,.2f}" if isinstance(_pred_val, (int, float)) else str(_pred_val)
        lines.append("Projection if unchanged:")
        lines.append(
            f"  {target_name}: projected {pred_val} over {p.get('horizon')} (model: {model_name})"
        )
        lines.append("")

    skeptic_arts = blackboard.by_type("skeptic")
    latest_verdict = skeptic_arts[-1].get("verdict") if skeptic_arts else None
    rejected = latest_verdict == "REJECT"
    # Claim state "CHALLENGED" only covers a REVISE verdict — REJECT maps to a
    # different state ("REJECTED", see coordinator/artifacts/claims.py's
    # _SKEPTIC_MAP) that this check used to miss entirely, letting a rejected
    # recommendation render as if nothing were wrong with it.
    unvalidated = bool(challenged) or rejected

    strategies = blackboard.by_type("strategy")
    if strategies and not unvalidated:
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
    elif strategies and rejected:
        s = strategies[0]
        lines.append("Recommendation (NOT validated — Skeptic REJECTED the underlying diagnosis):")
        for rec in s.get("recommended") or []:
            lines.append(f"  - {rec}")
        lines.append("  Do not act on this without further evidence.")
        lines.append("")

    if skeptic_arts:
        k = skeptic_arts[-1]
        verdict_text = {
            "PASS": "Independent verification passed.",
            "REVISE": "Independent verification found issues that need revision.",
            "REJECT": "Independent verification rejected this finding.",
        }.get(str(k.get("verdict") or ""), "Independent verification status is unclear.")
        lines.append(verdict_text)
        if k.get("required_followups"):
            lines.append("  Open questions before this can be trusted:")
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
    return _sanitize(text, challenged=unvalidated or latest_verdict == "REVISE", policies=policies)
