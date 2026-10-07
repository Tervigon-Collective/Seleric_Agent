"""Understand the question in one structured LLM call, before anything else runs.

Before 2026-10-07 the runner paid for three separate readings of the same question
before the agent's first tool call: Jev's classifier (~6s measured, labels close to
random live), a "value sense" helper call, and the planner's slot reader. On top of
that sat word lists — small talk, "yes"-style acceptances, "how's the business"
phrases — that routed by keyword.

One call now returns all of it: the question's shape and slots (``PlanSlots``, which
the plan template builds from), whether it continues the prior turn or accepts its
offer, which of the data-value words it uses as plain language, the time bucket and
direction it names, and — for small talk — the reply itself. Everything downstream
(intent, fast-model routing, tool budget, plan, prefetch) is derived from this one
reading in code.

Fail-open: no model, the offline ``TestModel``, or any error returns None and the
mission runs with no routing signal (the full agent loop), as when Jev was down.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import Field
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from seleric_swarm.agent.intent import QueryClassification
from seleric_swarm.agent.plan import PlanShape, PlanSlots
from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot

_log = logging.getLogger("seleric.agent.understand")

QuestionKind = Literal["conversation", "analysis", "overview", "forecast", "what_if", "action"]


class Understanding(PlanSlots):
    # Defaulted so a reply that omits it (small talk) is not a failed reading.
    shape: PlanShape = "other"
    kind: QuestionKind = Field(
        description=(
            "conversation: greeting, thanks, small talk or a question about the assistant — no "
            "business data asked. overview: how the business as a whole is doing, no specific "
            "metric named. forecast: a future value. what_if: a hypothetical scenario. action: "
            "create, change, pause, launch or delete something. analysis: everything else."
        )
    )
    reply: str = Field(
        default="",
        description="For kind=conversation only: the one or two sentence reply. No business numbers.",
    )
    follows_prior: bool = Field(
        default=False,
        description="True when the question only makes sense with the previous turn (it, those, same for X, why).",
    )
    accepts_offer: bool = Field(
        default=False,
        description="True when the user accepts the offer that ended the previous answer (yes, sure, go ahead).",
    )
    ordinary_words: list[str] = Field(
        default_factory=list,
        description="Of the listed data-value words, the ones the question uses as plain language, not as that value.",
    )
    grain: Literal["hour", "day", "week", "month", "none"] = Field(
        default="none", description="The time bucket the user asks to see results in, if any."
    )
    direction: Literal["increase", "decrease", "either"] = Field(
        default="either", description="The movement the user asks about, if any."
    )


_INSTRUCTIONS = (
    "Read the user's message to a business-analytics assistant and fill Understanding. Do not "
    "answer analytics questions and do not invent numbers.\n"
    "- kind, follows_prior, accepts_offer: judge from the message and the previous turn shown.\n"
    "- shape: entity_comparison when the question is about particular entities (the best or "
    "worst members of some dimension) in one window compared with another; period_comparison "
    "for whole-account figures across two windows; why_single_metric for why one metric's "
    "total moved; funnel for stage drop-off; else lookup, trend, breakdown or other. For a "
    "conversation use other.\n"
    "- entity_dimension: the dimension id (from the catalogue's Dimensions list) holding those "
    "entities' names.\n"
    "- rank_by: the outcome or efficiency measure that makes an entity 'best' — what it "
    "returned, never what it cost.\n"
    "- metrics: every measure asked for, one entry each, in plain words with abbreviations "
    "spelled out, plus your best metric id from the catalogue (empty if unsure). When the "
    "message continues the previous turn and names no measure, carry the previous turn's.\n"
    "- breakdown_dimensions: dimension ids for any 'by …' or 'map to …' the user asks for.\n"
    "- ordinary_words: only words from the 'Data-value words' list, and only when the message "
    "uses them as ordinary language (\"other days\", \"direct answer\") rather than meaning "
    "that recorded value."
)


@dataclass
class UnderstandOutcome:
    understanding: Understanding | None = None
    stats: dict[str, Any] = field(default_factory=dict)


def _prompt(
    query: str,
    *,
    catalogue: CatalogueSnapshot,
    value_words: list[str],
    prior_question: str = "",
    prior_offer: str = "",
) -> str:
    parts = [f"Message: {query}"]
    if prior_question or prior_offer:
        prior = ["Previous turn:"]
        if prior_question:
            prior.append(f"- the user asked: {prior_question[:300]}")
        if prior_offer:
            prior.append(f"- the answer ended by offering: {prior_offer[:300]}")
        parts.append("\n".join(prior))
    if value_words:
        parts.append("Data-value words (words in the message the data records as values): " + ", ".join(value_words))
    rendered = catalogue.render_compact()
    if rendered:
        parts.append(rendered)
    parts.append("Return Understanding now.")
    return "\n\n".join(parts)


async def understand(
    model: Model | None,
    query: str,
    *,
    catalogue: CatalogueSnapshot,
    value_words: list[str] = (),  # type: ignore[assignment]
    prior_question: str = "",
    prior_offer: str = "",
) -> UnderstandOutcome:
    if model is None or isinstance(model, TestModel):
        return UnderstandOutcome(stats={"status": "skipped"})
    started = time.perf_counter()
    stats: dict[str, Any] = {}
    try:
        reader: Agent[None, Understanding] = Agent(
            model=model, output_type=Understanding, instructions=_INSTRUCTIONS, name="seleric_understand"
        )
        result = await reader.run(
            _prompt(
                query,
                catalogue=catalogue,
                value_words=list(value_words),
                prior_question=prior_question,
                prior_offer=prior_offer,
            )
        )
        usage = result.usage() if callable(result.usage) else result.usage
        stats.update(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
        understanding = result.output
    except Exception as exc:
        _log.warning("understand_failed", exc_info=True)
        stats.update(status="failed", error=type(exc).__name__)
        return UnderstandOutcome(stats=_finish(stats, started))
    stats.update(status="ok", kind=understanding.kind, shape=understanding.shape)
    return UnderstandOutcome(understanding=understanding, stats=_finish(stats, started))


def _finish(stats: dict[str, Any], started: float) -> dict[str, Any]:
    stats["latency_ms"] = round((time.perf_counter() - started) * 1000)
    _log.info("understand_stats %s", stats)
    return stats


_SHAPE_INTENT = {
    "lookup": "lookup",
    "trend": "trend",
    "breakdown": "aggregation",
    "period_comparison": "comparison",
    "entity_comparison": "comparison",
    "why_single_metric": "diagnostic",
    "funnel": "diagnostic",
}
_KIND_INTENT = {
    "conversation": "conversation",
    "overview": "aggregation",
    "forecast": "forecast",
    "what_if": "simulation",
}


def classification_from(u: Understanding | None) -> QueryClassification:
    """The routing signals the runner uses, derived in code from the one reading.
    None (understanding unavailable) -> an empty classification: no routing signal."""
    if u is None:
        return QueryClassification()
    intent = _KIND_INTENT.get(u.kind) or _SHAPE_INTENT.get(u.shape)
    if intent in ("diagnostic", "simulation") or u.shape == "entity_comparison":
        complexity = "complex"
    elif intent == "comparison" or len(u.metrics) > 3:
        complexity = "moderate"
    else:
        complexity = "simple"
    return QueryClassification(
        intent=intent,  # type: ignore[arg-type]
        complexity=complexity,  # type: ignore[arg-type]
        needs_write=u.kind == "action",
        grain=u.grain if u.grain in ("day", "week", "month", "none") else None,  # type: ignore[arg-type]
        period=None,  # windows come from the deterministic time resolver
        direction=u.direction,
        depends_on_prior=u.follows_prior or u.accepts_offer,
    )
