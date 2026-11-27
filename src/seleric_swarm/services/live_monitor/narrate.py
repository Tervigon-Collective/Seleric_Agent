"""Plain-language narration of a finding's change (opened, reopened, severity changed, resolved).

Called only on a state change, never on a steady tick. The rule's own summary is the fallback, so a slow or failed LLM
never blocks or blanks a notification. The model gets the numbers, not the account: no tools, no writes.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMRequestMetadata

log = logging.getLogger(__name__)
PROMPT_ID = "live_monitor.narrate"
PROMPT_VERSION = "1"
MAX_CHARS = 420

SYSTEM = """You write one notification for a performance marketer watching live Meta ads.
Say what changed and why it matters in at most 3 short sentences (under 400 characters), using only the numbers given.
Use rupees as ₹ with thousands separators. Name the campaign / ad set / ad. Do not invent causes; if the evidence
suggests one (for example CTR normal but conversions down points past the ad), say "likely". If the change is a
resolution, say what recovered. If the evidence has learning=true, say it is still learning. No greetings, no markdown."""


def fallback(change: str, title: str, summary: str, path: list[str]) -> str:
    where = " › ".join(path) if path else "Account"
    if change == "resolved":
        return f"Resolved: {title} on {where}."
    return f"{where}: {summary}"


def _payload(change: str, finding: dict[str, Any], guardrails: dict[str, Any]) -> str:
    d = finding["diagnosis"]
    return json.dumps({
        "change": change, "rule": finding["title"], "severity": finding["severity"], "summary": finding["summary"],
        "where": d.get("entity", {}).get("path"), "level": d.get("entity", {}).get("level"), "evidence": d.get("evidence"),
        "estimated_impact_inr": finding.get("impact"), "guardrails": {k: guardrails.get(k) for k in ("be_roas_platform", "target_roas", "allowable_cpa")},
    }, default=str)


async def narrate(llm: Any, model: str, change: str, finding: dict[str, Any], guardrails: dict[str, Any]) -> tuple[str, str]:
    """(text, source) where source is "llm" or "rule"."""
    d = finding["diagnosis"]
    fb = fallback(change, finding["title"], finding["summary"], list(d.get("entity", {}).get("path") or []))
    if llm is None:
        return fb, "rule"
    req = LLMRequest(
        model=model, temperature=0.2, max_tokens=220, timeout_s=20.0,
        messages=[ChatMessage(role="system", content=SYSTEM), ChatMessage(role="user", content=_payload(change, finding, guardrails))],
        metadata=LLMRequestMetadata(
            request_id=str(uuid.uuid4()), session_id=f"live-monitor:{finding['brand_id']}", mission_id=finding["finding_id"],
            workflow_name="live_monitor", workflow_version="1", agent_id="live_monitor", agent_version="1",
            prompt_id=PROMPT_ID, prompt_version=PROMPT_VERSION, model=model or None,
        ),
        tags=["live_monitor", "narrate"],
    )
    try:
        out = (await llm.complete(req)).text.strip()
    except Exception:
        log.warning("narration failed for %s; using the rule text", finding["finding_id"], exc_info=True)
        return fb, "rule"
    if not out:
        return fb, "rule"
    return (out[: MAX_CHARS - 1] + "…") if len(out) > MAX_CHARS else out, "llm"
