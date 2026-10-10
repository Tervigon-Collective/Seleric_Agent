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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from seleric_swarm.agent.intent import QueryClassification
from seleric_swarm.agent.plan import MetricSlot, PlanShape, PlanSlots
from seleric_swarm.agent.query_spec import WindowSlot
from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot

_log = logging.getLogger("seleric.agent.understand")

QuestionKind = Literal["conversation", "analysis", "overview", "forecast", "what_if", "action"]
HorizonUnit = Literal["day", "week", "month"]
PeriodWord = Literal["next_n", "rest_of_month", "next_month", "this_quarter"]


class ForecastHorizonSlot(BaseModel):
    n: int = Field(default=14, description="How many units ahead when period_word is next_n.")
    unit: HorizonUnit = Field(default="day", description="day, week or month.")
    until_iso: str = Field(
        default="",
        description="Inclusive end date YYYY-MM-DD when the user names one; else empty.",
    )
    period_word: PeriodWord = Field(
        default="next_n",
        description=(
            "next_n: the next n units; rest_of_month: through month-end; "
            "next_month: the following calendar month; this_quarter: through quarter-end."
        ),
    )


class ForecastSlots(BaseModel):
    """Filled only when kind=forecast. Targets reuse MetricSlot."""

    targets: list[MetricSlot] = Field(
        default_factory=list,
        description="Metrics to forecast; empty means use Understanding.metrics.",
    )
    horizon: ForecastHorizonSlot = Field(
        default_factory=ForecastHorizonSlot,
        description="How far ahead to forecast from as_of.",
    )


class Understanding(PlanSlots):
    # The slots the plan is built from are required, not defaulted. With every slot
    # defaulted the planner model (grok) returned only ``kind`` — 42 output tokens —
    # and every mission ran unplanned: shape "other", no metrics, no entity (live
    # 2026-10-07 MS3-cbf57264a7, 7 of 7 analysis missions "no usable step"). Required
    # fields make the model state them; a conversation states "other" and [] in a
    # few tokens.
    shape: PlanShape = Field(
        description="The question's shape (see the instructions); other for a conversation."
    )
    entity_dimension: str = Field(
        description="For entity_comparison: the catalogue dimension id that holds the entities' names; else ''."
    )
    rank_by: MetricSlot | None = Field(
        description="What makes an entity 'best' / 'top' (an outcome or efficiency measure); null when nothing is ranked."
    )
    rank_order: Literal["desc", "asc"] = Field(
        default="desc",
        description="asc when the user asks for the worst, lowest, loss-making or declining entities; else desc.",
    )
    metrics: list[MetricSlot] = Field(
        description="Every measure the user asks for, one entry each; [] only when the message names none."
    )
    breakdown_dimensions: list[str] = Field(
        description="Catalogue dimension ids the user asks to break down or map by; [] when none."
    )
    breakdown_words: str = Field(
        description=(
            "The user's own words asking to split, group or compare results across the values of a dimension "
            "('by channel', 'per product', 'split by platform'), quoted from the message; '' when there are none. "
            "When the message names the entities it wants (campaigns, ads, products), those entities are the rows: "
            "a dimension that only describes where they run or which figures to read ('metrics for the ads "
            "platform', 'on Meta') is NOT a split unless the message says to split, group or compare across it "
            "('by', 'per', 'across', 'split by')."
        )
    )
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
    restyles_prior: bool = Field(
        default=False,
        description=(
            "True when the message only asks to see the previous answer in another form (as a graph, chart, "
            "table, shorter, in lakhs) and names no new measure, entity or period of its own."
        ),
    )
    ordinary_words: list[str] = Field(
        default_factory=list,
        description="Of the listed data-value words, the ones the question uses as plain language, not as that value.",
    )
    names_period: bool = Field(
        description=(
            "True when the message names or implies a time period of its own (a date, yesterday, "
            "last 7 completed days, this month, since Monday); false when it names none."
        )
    )
    through_hour: int | None = Field(
        default=None,
        description=(
            "The hour of day (1-24, 24-hour clock) the user cuts a same-hours comparison at: "
            "'till 11 am' -> 11, 'up to 3 pm' -> 15; null when no time of day is named."
        ),
    )
    grain: Literal["hour", "day", "week", "month", "none"] = Field(
        default="none", description="The time bucket the user asks to see results in, if any."
    )
    direction: Literal["increase", "decrease", "either"] = Field(
        default="either", description="The movement the user asks about, if any."
    )
    part_of_whole: bool = Field(
        default=False,
        description=(
            "True only when the user asks how much a named value (a channel, platform, product …) explains or "
            "contributes to a change or total of the whole ('is Meta responsible for the drop', 'how much of the "
            "decline is Google'). False when they ask for the named value's own figures ('WhatsApp orders "
            "yesterday', 'Meta campaigns last week')."
        ),
    )
    forecast: ForecastSlots | None = Field(
        default=None,
        description=(
            "When kind=forecast: targets and horizon slots. Null for every other kind. "
            "targets may be empty when metrics already lists them."
        ),
    )
    windows: list[WindowSlot] = Field(
        default_factory=list,
        description=(
            "Every time window the message names, as expressions (not calendar dates). "
            "role=event for the period asked about; role=baseline for what to compare it "
            "against (the days/weeks before the event, or an explicitly named prior period); "
            "role=context for a surrounding span the user also names (e.g. 'over the last 3 "
            "days' when the event is yesterday). Prefer name=yesterday|today|this_week|… when "
            "those words appear; else unit+n with ending=yesterday for 'last N days/weeks/…'; "
            "use ending=before_event on a baseline that is 'the N days before' the event; use "
            "start_iso/end_iso only for explicit calendar dates. Empty when no period is named."
        ),
    )


_INSTRUCTIONS = (
    "Read the user's message to a business-analytics assistant and fill Understanding. Do not "
    "answer analytics questions and do not invent numbers.\n"
    "- kind, follows_prior, accepts_offer: judge from the message and the previous turn shown.\n"
    "- forecast: only when kind=forecast — set horizon (n/unit/period_word/until_iso) from the "
    "message (default next 14 days); leave null otherwise.\n"
    "- shape: entity_comparison when the question is about particular entities (the best or "
    "worst members of some dimension) in one window compared with another — not a question about ONE "
    "entity it writes out (one campaign, ad or product) against its own other days, which is "
    "why_single_metric; period_comparison "
    "for whole-account figures across two windows; why_single_metric for why one metric's "
    "total moved — also when it names possible causes or drivers and asks to rank them; "
    "composition when the user wants a total split into the additive P&L/waterfall lines it is "
    "made of (reconcile, waterfall, what makes up profit/margin/net sales, how much of a change "
    "came from each cost or revenue line) — list that total first in metrics; NOT a bare "
    "'breakdown' / 'break it down' / 'by day' follow-up (that is shape breakdown: split by day, "
    "channel, product, …). Funnel for stage drop-off; else lookup, trend, breakdown or other. "
    "For a conversation use other.\n"
    "- entity_dimension: the dimension id (from the catalogue's Dimensions list) holding those "
    "entities' names.\n"
    "- rank_by: the outcome or efficiency measure that makes an entity 'best' — what it "
    "returned, never what it cost. When the question selects entities by a condition on one "
    "measure (losing money, below target, declining), rank_by is that measure so every entity "
    "meeting it is found — not the measure they are merely described by.\n"
    "- rank_order: asc when the entities wanted are the worst on rank_by (lowest, losing, "
    "declining, dragging the total down); desc otherwise.\n"
    "- metrics: every measure asked for, one entry each, in plain words with abbreviations "
    "spelled out, plus your best metric id from the catalogue (empty if unsure). One entry per "
    "measure the user names: never split it into variants they did not name (gross / net, "
    "platform / blended) and never add a qualifier they did not say (a date basis such as P&L / "
    "finance / order date, a channel, gross / net) — the catalogue knows the default meaning. For "
    "composition, list only the total the user wants split; the catalogue knows its lines. When the "
    "message continues the previous turn and names no measure, carry the previous turn's. When "
    "the message asks how entities or a channel perform (their performance, how they did) and "
    "names no measure, list the measures an analyst judges that kind of entity by: what was "
    "spent on it, what it returned, and its efficiency and delivery rates. For why_single_metric "
    "the first entry is the outcome whose move is explained: when the user asks why something did "
    "not perform, lost money or did badly and names no measure, that is what it returned for what it "
    "cost (its profit, or its return on ad spend) — never the spend itself, which follows as a "
    "possible explanation with the delivery and conversion rates.\n"
    "- breakdown_dimensions: dimension ids for any 'by …' or 'map to …' the user asks for — the "
    "entity's name dimension (its title / name), not its id, unless the user asks for ids. A word that "
    "only says where the named entities live or what kind of figures to read (\"check all the metrics "
    "for the ads platform\" about two named campaigns) is not a breakdown: [] unless the user asks to "
    "split by it.\n"
    "- ordinary_words: decide for each word in the 'Data-value words' list whether the message "
    "means that value of the dimension it is recorded under. List every word it does not mean "
    "that way: ordinary language (\"other days\", \"direct answer\"), or the same word meant "
    "for a different kind of thing than that dimension holds. Words meant as that value stay out.\n"
    "- windows: every period the message names, as expressions — not dates. A diagnosis that "
    "asks why something moved yesterday against the days before it has event=yesterday and a "
    "baseline with unit=day, n=how many days before, ending=before_event; 'compared to the other "
    "days', 'its usual' or 'normal days' with no count is that baseline with n=7. 'Over the last 3 "
    "days, why did X fall yesterday' also has a context window of last 3 days. A plain "
    "'net sales last week' has one event window (name=last_week). Do not invent a baseline "
    "the user did not ask for. Leave [] when no period is named."
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
    value_meanings: Mapping[str, str] | None = None,
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
        # Where each word is recorded: the bare word could not tell "other sources"
        # from the payment_method value "other" (golden Q17, 2026-10-08).
        meanings = value_meanings or {}
        parts.append(
            "Data-value words (words in the message the data records as values, and where):\n"
            + "\n".join(f"- {w}" + (f": recorded as {meanings[w]}" if meanings.get(w) else "") for w in value_words)
        )
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
    value_meanings: Mapping[str, str] | None = None,
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
                value_meanings=value_meanings,
                prior_question=prior_question,
                prior_offer=prior_offer,
            )
        )
        usage = result.usage() if callable(result.usage) else result.usage
        stats.update(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
        understanding = result.output
        if understanding.breakdown_dimensions and not understanding.breakdown_words.strip():
            # A breakdown the user never asked for becomes a hard scope requirement: two named campaigns read as
            # "by ad platform" sent the answer back four times for a grouping nobody wanted (live 2026-10-10).
            understanding = understanding.model_copy(update={"breakdown_dimensions": []})
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
    "composition": "aggregation",
    "funnel": "diagnostic",
}
_KIND_INTENT = {
    "conversation": "conversation",
    "overview": "aggregation",
    "forecast": "forecast",
    "what_if": "simulation",
}


def diagnose_one_named_entity(
    u: Understanding | None,
    value_filters: Sequence[Any],
    catalogue: CatalogueSnapshot,
    outcome_candidates: Sequence[str] = (),
) -> Understanding | None:
    """One entity the question names, read against its own other days, is diagnosed — not compared with its peers.

    "TH-383-SUSPENDER-26SEP-ADV+, why did it not perform yesterday compared to the other days" was read as an
    entity comparison three times in five: the plan ranked every campaign by spend and the answer explained orders
    through site searches (live 2026-10-10 thread_066b9cd1). When exactly one value of the entity's kind is named,
    the shape is why_single_metric: the outcome is what the entity is judged by (``rank_by``, else the first
    ``outcome_candidates`` ratio the entity carries) and the measures read become the levers checked against the
    entity's own days. Several named values stay a comparison."""
    if u is None or u.shape != "entity_comparison" or not u.entity_dimension.strip():
        return u
    family = catalogue.family_members(u.entity_dimension.strip())
    named = [vf for vf in value_filters if set(getattr(vf, "dimensions", ())) & family]
    if len(named) != 1 or len(getattr(named[0], "values", ())) != 1:
        return u
    dims = {*family, *named[0].dimensions}
    outcome = u.rank_by if u.rank_by is not None and u.rank_by.metric_id else None
    if outcome is None:
        # A headline ratio the reading already holds first (net ROAS read among the levers, not MER, which only
        # leads the headline), then the headline's own order.
        read = {s.metric_id for s in u.metrics}
        ordered = [*(m for m in outcome_candidates if m in read), *(m for m in outcome_candidates if m not in read)]
        oid = next(
            (m for m in ordered
             if catalogue.aggregation_for(m) == "ratio" and any(catalogue.carries(m, d) for d in dims)),
            None,
        )
        if oid is not None:
            outcome = next((s for s in u.metrics if s.metric_id == oid), None) or MetricSlot(
                words=catalogue.label_for(oid) or oid.replace("_", " "), metric_id=oid
            )
    metrics = list(u.metrics)
    if outcome is not None:
        metrics = [outcome, *(s for s in metrics if s.metric_id != outcome.metric_id)]
    return u.model_copy(update={"shape": "why_single_metric", "metrics": metrics, "rank_by": None})


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
