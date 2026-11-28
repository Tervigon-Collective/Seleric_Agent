"""Structured diagnosis for a live finding: deterministic gates + artifacts only (B31).

Never invents a breach — it only surfaces gates for a hit that already passed the rule's statistical gates.
Cause / bullets come from the Marketing bot write-back (``bots.finding_handoff``); this module leaves
``status=pending_bot`` until then. Recommended action is always chosen from the finding's allow-listed actions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

ACTOR = "live_monitor.diagnose"
PENDING_CAUSE = "Marketing bot investigating"


def gates_from_evidence(evidence: dict[str, Any], confidence: float | None) -> dict[str, Any]:
    """Surfaces the gates the rule already applied; does not recompute probabilities."""
    learning = evidence.get("learning") is True
    window = None
    for key in ("spend_3d", "purchases_3d", "roas_3d", "cpa_3d"):
        if key in evidence:
            window = "3d"
            break
    if window is None and any(k.endswith("_today") or k in ("spend_today", "impressions_today", "ctr_today") for k in evidence):
        window = "today"
    if window is None and any(k.startswith("p_below") or k.startswith("p_above") for k in evidence):
        window = "3d"
    conf = confidence if confidence is not None else evidence.get("p_below_target") or evidence.get("p_below_be") or evidence.get("p_above_3d") or evidence.get("p_below_p10")
    if isinstance(conf, (int, float)):
        conf = round(float(conf), 3)
    else:
        conf = None
    min_volume_ok = True
    if evidence.get("spend_3d") is not None and evidence.get("spend_3d", 0) == 0:
        min_volume_ok = False
    return {
        "confidence": conf,
        "min_volume_ok": min_volume_ok,
        "learning": learning,
        "window": window,
    }


def artifacts_from_evidence(evidence: dict[str, Any], summary: str) -> list[dict[str, Any]]:
    """Deterministic reviewable rows from the hit evidence only — no invented numbers."""
    out: list[dict[str, Any]] = []
    metric_keys = [
        "spend_today", "expected_by_now", "daily_budget", "pace",
        "spend_3d", "purchases_3d", "purchase_value_3d", "roas_3d", "cpa_3d",
        "target_roas", "be_roas", "allowable_cpa",
        "p_below_target", "p_below_be", "p_below_be_7d", "p_above_3d",
        "ctr_today", "ctr_p10", "ctr_p50", "impressions_today", "p_below_p10",
        "cpm", "cpm_p90", "frequency", "ctr_drop",
        "lpv_per_click", "clicks", "url", "url_ok", "url_status",
        "learning_status", "conversions", "expected_impressions",
        "ratio", "usual_ratio", "pixel_purchases", "store_orders", "atc_7d", "purchases_7d",
    ]
    rows = []
    for k in metric_keys:
        if k in evidence and evidence[k] is not None:
            rows.append({"key": k, "value": evidence[k]})
    if rows:
        out.append({"kind": "metric_row", "title": "Observed", "payload": {"rows": rows}, "source": "rule.evidence"})
    if evidence.get("url"):
        out.append({
            "kind": "url_check", "title": "Landing URL",
            "payload": {"url": evidence.get("url"), "ok": evidence.get("url_ok"), "status": evidence.get("url_status")},
            "source": "landing_break",
        })
    if evidence.get("also_below"):
        out.append({
            "kind": "table", "title": "Also below",
            "payload": {"names": evidence.get("also_below"), "count": evidence.get("also_below_count")},
            "source": "rollup",
        })
    if evidence.get("also"):
        out.append({
            "kind": "quote", "title": "Also flagged",
            "payload": {"items": evidence.get("also")},
            "source": "supersede",
        })
    if not out:
        out.append({"kind": "quote", "title": "Rule", "payload": {"text": summary}, "source": "rule.summary"})
    return out


def pick_recommended(actions: list[dict[str, Any]], preferred: str | None = None) -> tuple[str | None, str | None]:
    """Return (action_id, title). Prefer preferred if allow-listed; else first non-link; else open_agent."""
    ids = {a["id"]: a for a in actions if a.get("id")}
    if preferred and preferred in ids:
        a = ids[preferred]
        return a["id"], a.get("title") or a["id"]
    for a in actions:
        if a.get("risk") != "link":
            return a["id"], a.get("title") or a["id"]
    for a in actions:
        if a.get("id") == "open_agent":
            return a["id"], a.get("title") or "Open in Marketing Agent"
    if actions:
        a = actions[0]
        return a["id"], a.get("title") or a["id"]
    return None, None


def fallback_cause(title: str, summary: str, path: list[str]) -> str:
    where = " › ".join(path) if path else "Account"
    return f"{title} on {where}: {summary}"


def fallback_bullets(evidence: dict[str, Any], gates: dict[str, Any]) -> list[str]:
    bullets: list[str] = []
    if gates.get("confidence") is not None:
        bullets.append(f"Confidence {gates['confidence']:.0%} against the configured line.")
    if gates.get("learning"):
        bullets.append("Still in learning: efficiency capped at info.")
    if gates.get("window"):
        bullets.append(f"Judged on the {gates['window']} window.")
    for k in ("spend_3d", "spend_today", "roas_3d", "cpa_3d", "ctr_today"):
        if k in evidence and evidence[k] is not None:
            bullets.append(f"{k.replace('_', ' ')}: {evidence[k]}")
        if len(bullets) >= 4:
            break
    return bullets[:4] or ["See the observed metrics for details."]


async def diagnose(
    llm: Any,
    model: str,
    finding: dict[str, Any],
    guardrails: dict[str, Any],
    *,
    change: str,
) -> dict[str, Any]:
    """Gates + artifacts only. ``llm`` / ``model`` / ``guardrails`` kept for call-site compatibility; unused (B31)."""
    del llm, model, guardrails  # B31: no diagnose LLM — bot write-back fills cause/bullets
    d = finding.get("diagnosis") or {}
    evidence = dict(d.get("evidence") or {})
    actions = list(d.get("actions") or [])
    gates = gates_from_evidence(evidence, finding.get("confidence"))
    artifacts = artifacts_from_evidence(evidence, finding.get("summary") or "")
    rec_id, rec_title = pick_recommended(actions)
    now = datetime.now().astimezone().isoformat()
    return {
        "status": "pending_bot",
        "cause": PENDING_CAUSE,
        "bullets": [],
        "artifacts": artifacts,
        "gates": gates,
        "recommended_action_id": rec_id,
        "recommended_summary": rec_title,
        "diagnosed_at": now,
        "diagnosed_by": ACTOR,
        "diagnosis_source": "awaiting_bot",
        "narration": PENDING_CAUSE,
        "narration_source": "awaiting_bot",
        "narration_change": change,
        "narrated_at": now,
    }
