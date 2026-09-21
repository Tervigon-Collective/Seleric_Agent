"""Plan-first step — one bounded LLM call that drafts an ordered plan before
the main agent loop, so the ReAct loop executes with fewer wrong turns.

Inputs: the user query, the Jev intent label (``agent/intent.py`` routing
hint), the capability manifest (every tool the agent can call) and the whole
catalogue snapshot. The plan is prepended to the mission prompt and stored as
a ``ui`` artifact for observability.

Fail-open: any error — or the offline stub ``TestModel`` — returns ``None`` and
the mission runs exactly as it did before this step existed.
"""

from __future__ import annotations

import logging

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot

_log = logging.getLogger("seleric.agent.plan")

_PLAN_INSTRUCTIONS = (
    "You are the planning step for the Seleric analytics agent. Given a user "
    "question, its detected intent, the available tools, and the metric "
    "catalogue, write a short ordered plan (2-5 numbered steps) naming which "
    "tool to call at each step and the exact catalogue metric id(s) to use. "
    "Resolve every metric id against the catalogue — never invent one. Do not "
    "answer the question or produce any numbers; output only the plan."
)


def _plan_prompt(
    query: str,
    intent: str | None,
    manifest: str,
    catalogue: CatalogueSnapshot,
) -> str:
    parts = [f"Question: {query}"]
    if intent:
        parts.append(f"Detected intent: {intent}")
    parts.append(manifest)
    rendered = catalogue.render()
    if rendered:
        parts.append(rendered)
    parts.append("Write the plan now.")
    return "\n\n".join(parts)


async def build_plan(
    model: Model | None,
    *,
    query: str,
    intent: str | None,
    manifest: str,
    catalogue: CatalogueSnapshot,
) -> str | None:
    """Draft an ordered plan, or None if planning is unavailable/failed."""
    if model is None or isinstance(model, TestModel):
        return None
    try:
        planner: Agent[None, str] = Agent(
            model=model,
            output_type=str,
            instructions=_PLAN_INSTRUCTIONS,
            name="seleric_planner",
        )
        result = await planner.run(_plan_prompt(query, intent, manifest, catalogue))
        text = (result.output or "").strip()
        return text or None
    except Exception:
        _log.warning("plan_step_failed", exc_info=True)
        return None
