"""D-253: a run's answer as data for the Bots inbox (proposal, checks, movers), stored next to the Markdown answer.

Checks with a normal range come from the run's own ``bot_eda`` bands, in code. Proposals, movers and the bot's other
checks are read out of its answer by one structured helper call. Nothing here changes the answer, the email or the
verdict; a failed extraction leaves only the code checks and the page falls back to the Markdown.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from seleric_swarm.services.bots import eda

_log = logging.getLogger("seleric.bots")

# where_today_sits metrics the page shows, in this order, with the unit the UI formats them in
METRICS: dict[str, tuple[str, str]] = {
    "ad_spend": ("Ad spend so far", "inr"),
    "link_clicks": ("Link clicks so far", "count"),
    eda.LPV: ("Landing page views so far", "count"),
    "ctr_link": ("Link CTR", "pct"),
    "lpv_per_click": ("Landing page views per click", "pct"),
}


class Proposal(BaseModel):
    entity: str = Field(description="Campaign, ad set, ad or 'Account', copied exactly as the answer writes it")
    field: str = Field("", description="What would change, e.g. 'daily budget' or 'status'; empty when nothing changes")
    current: float | None = Field(None, description="Current value as a plain number in rupees (no ₹, no commas); null when the answer gives none")
    proposed: float | None = Field(None, description="Proposed value as a plain number in rupees; null when it is not a value change")
    condition: str = Field("", description="When to apply it, e.g. 'only if short-window CPA > 7-day CPA'; empty when unconditional")
    reason: str = Field("", description="The answer's reason, one or two short sentences")


class Mover(BaseModel):
    entity: str = Field(description="Name copied exactly as the answer writes it")
    figure: str = Field("", description="Its figure as the answer writes it, e.g. '₹1,802'")
    note: str = Field("", description="Why it matters, a short phrase")


class Check(BaseModel):
    label: str = Field(description="Short metric name without the time window, e.g. 'Impressions'")
    now: str = ""
    normal: str = Field("", description="Normal range as written; empty for '–'")
    read: str = Field("", description="One or two words: Normal, High, Low, Too early")


class Extracted(BaseModel):
    headline: str = Field("", description="The answer's bold first sentence, without asterisks")
    proposals: list[Proposal] = Field(default_factory=list, description="One per 'What to do' bullet; empty when it says nothing to change")
    movers: list[Mover] = Field(default_factory=list)
    checks: list[Check] = Field(default_factory=list, description="Rows of the checks table, except rows measuring a metric listed as already shown, however the row words it")
    unverified: list[str] = Field(default_factory=list, description="Figures the answer says could not be checked on live data")


def read_of(now: float, p10: float, p90: float) -> Literal["Low", "Normal", "High"]:
    return "Low" if now < p10 else "High" if now > p90 else "Normal"


def code_checks(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Banded checks from the run's latest completed bot_eda: today so far against p10 / p50 / p90 for the same hours."""
    got = [s["bands"] for s in steps if s.get("kind") == "tool_return" and s.get("bands")]
    if not got:
        return []
    out = []
    for metric, (label, unit) in METRICS.items():
        b = got[-1].get(metric) or {}
        now, p10, p90 = b.get("today_so_far"), b.get("p10"), b.get("p90")
        if None in (now, p10, p90):
            continue
        out.append({"metric": metric, "label": label, "unit": unit, "now": now, "p10": p10, "p50": b.get("p50"),
                    "p90": p90, "read": read_of(now, p10, p90)})
    return out


SYSTEM = ("You turn a marketing bot's answer into JSON for an inbox. Copy names and figures exactly as the answer has "
          "them; never add facts, never compute. Rupee values become plain numbers (₹3,000 → 3000). Use null or an "
          "empty list when the answer does not say.")


async def extract(runtime: Any, answer: str, shown: list[str]) -> Extracted | None:
    from seleric_swarm.config.settings import helper_chat_model
    from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMRequestMetadata

    model = helper_chat_model(runtime.settings)  # "" = the adapter's primary model
    skip = (f"Already shown from code, so leave out of checks every row that measures one of these, however it is worded "
            f"(e.g. 'ad spend' matches 'Ad spend so far'): {'; '.join(shown)}.\n\n") if shown else ""
    try:
        res = await runtime.llm.complete_structured(LLMRequest(
            messages=[ChatMessage(role="system", content=SYSTEM), ChatMessage(role="user", content=f"{skip}Answer:\n{answer}")],
            model=model, temperature=0, max_tokens=4000, timeout_s=60,
            metadata=LLMRequestMetadata(agent_id="bot_report", query_class="bot_report"), tags=["bot_report"]), Extracted)
    except Exception:  # the inbox falls back to the Markdown answer
        _log.warning("bot_report_extract_failed", exc_info=True)
        return None
    return res.value if isinstance(res.value, Extracted) else Extracted.model_validate(res.value)


async def build(runtime: Any, run: dict[str, Any], answer: str, steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The run's report, or None for chat runs and runs without an answer."""
    if run["trigger_kind"] == "chat" or not answer.strip():
        return None
    checks = code_checks(steps)
    got = await extract(runtime, answer, [c["label"] for c in checks])
    return {"version": 1, "eda_checks": checks, **(got.model_dump() if got else {"extract_failed": True})}
