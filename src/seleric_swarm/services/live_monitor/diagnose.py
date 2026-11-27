"""Structured diagnosis for a live finding: deterministic gates + artifacts, optional LLM cause.

Never invents a breach — it only explains a hit that already passed the rule's statistical gates.
Recommended action is always chosen from the finding's allow-listed actions.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMRequestMetadata

log = logging.getLogger(__name__)
PROMPT_ID = "live_monitor.diagnose"
PROMPT_VERSION = "1"
ACTOR = "live_monitor.diagnose"

SYSTEM = """You diagnose one live Meta ads alert for a performance marketer.
Reply with ONLY a JSON object: {"cause": "<one sentence>", "bullets": ["claim", ...], "recommended_action_id": "<id or null>"}.
Use only the numbers and evidence given. Do not invent causes. bullets: 2 to 4 short checkable claims.
recommended_action_id must be one of the allowed_action_ids (or null). No markdown, no prose outside JSON."""


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


def _parse_llm(text: str, allowed: set[str]) -> dict[str, Any] | None:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    rid = data.get("recommended_action_id")
    if rid is not None and rid not in allowed:
        data["recommended_action_id"] = None
    bullets = data.get("bullets")
    if not isinstance(bullets, list):
        data["bullets"] = []
    else:
        data["bullets"] = [str(b)[:200] for b in bullets[:4]]
    cause = data.get("cause")
    data["cause"] = str(cause)[:400] if cause else None
    return data


async def diagnose(
    llm: Any,
    model: str,
    finding: dict[str, Any],
    guardrails: dict[str, Any],
    *,
    change: str,
) -> dict[str, Any]:
    """Returns diagnosis fields to merge into finding.diagnosis (status ready|failed)."""
    d = finding.get("diagnosis") or {}
    evidence = dict(d.get("evidence") or {})
    actions = list(d.get("actions") or [])
    path = list((d.get("entity") or {}).get("path") or [])
    gates = gates_from_evidence(evidence, finding.get("confidence"))
    artifacts = artifacts_from_evidence(evidence, finding.get("summary") or "")
    allowed = {a["id"] for a in actions if a.get("id")}
    cause = fallback_cause(finding.get("title") or d.get("title") or "Alert", finding.get("summary") or "", path)
    bullets = fallback_bullets(evidence, gates)
    rec_id, rec_title = pick_recommended(actions)
    source = "rule"

    if llm is not None:
        payload = json.dumps({
            "change": change,
            "rule": finding.get("title") or d.get("title"),
            "severity": finding.get("severity"),
            "summary": finding.get("summary"),
            "where": path,
            "level": (d.get("entity") or {}).get("level"),
            "evidence": evidence,
            "gates": gates,
            "allowed_action_ids": sorted(allowed),
            "guardrails": {k: guardrails.get(k) for k in ("be_roas_platform", "target_roas", "allowable_cpa")},
        }, default=str)
        req = LLMRequest(
            model=model, temperature=0.1, max_tokens=350, timeout_s=25.0,
            messages=[ChatMessage(role="system", content=SYSTEM), ChatMessage(role="user", content=payload)],
            metadata=LLMRequestMetadata(
                request_id=str(uuid.uuid4()), session_id=f"live-monitor:{finding.get('brand_id')}",
                mission_id=finding.get("finding_id"), workflow_name="live_monitor", workflow_version="1",
                agent_id="live_monitor", agent_version="1", prompt_id=PROMPT_ID, prompt_version=PROMPT_VERSION,
                model=model or None,
            ),
            tags=["live_monitor", "diagnose"],
        )
        try:
            parsed = _parse_llm((await llm.complete(req)).text, allowed)
            if parsed:
                if parsed.get("cause"):
                    cause = parsed["cause"]
                if parsed.get("bullets"):
                    bullets = parsed["bullets"]
                if parsed.get("recommended_action_id"):
                    rec_id, rec_title = pick_recommended(actions, parsed["recommended_action_id"])
                source = "llm"
        except Exception:
            log.warning("diagnose LLM failed for %s; using rule fallback", finding.get("finding_id"), exc_info=True)

    return {
        "status": "ready",
        "cause": cause,
        "bullets": bullets,
        "artifacts": artifacts,
        "gates": gates,
        "recommended_action_id": rec_id,
        "recommended_summary": rec_title,
        "diagnosed_at": datetime.now().astimezone().isoformat(),
        "diagnosed_by": ACTOR,
        "diagnosis_source": source,
        "narration": cause,
        "narration_source": source,
        "narration_change": change,
        "narrated_at": datetime.now().astimezone().isoformat(),
    }
