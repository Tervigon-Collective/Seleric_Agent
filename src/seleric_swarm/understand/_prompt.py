"""Prompt builder for the understand LLM call (M1.1).

Builds the prompt ONLY from the read model: concept labels, dimension labels,
hierarchy level names, and value-candidate words. NO metric or channel words
in the prompt text (Rule A2).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from seleric_swarm.semantic import SemanticReadModel

INSTRUCTIONS = (
    "Read the user's message to a business-analytics assistant and fill QuestionDraft, or return "
    "Clarification when the question cannot be read without asking (see needs_clarification). Do not "
    "answer analytics questions and do not invent numbers.\n"
    "- kind: conversation for a greeting, thanks, small talk or a question about the assistant (put the "
    "one or two sentence reply in reply); overview for how the business as a whole is doing with no "
    "measure named; forecast for a future value; what_if for a hypothetical; action to create, change, "
    "pause, launch or delete something; exploration for open questions ('anything unusual?', 'what "
    "should I look at?'); analysis for everything else.\n"
    "- follows_prior, accepts_offer, restyles_prior: judge from the message and the previous turn shown. "
    "restyles_prior is a request to show the previous answer in another form and names no new measure, "
    "entity or period.\n"
    "- shape: entity_comparison when the question is about particular members of a dimension in one "
    "window against another; why_single_metric for why one measure's total moved (also when it names "
    "candidate causes); period_comparison for whole-account figures across two windows; composition when "
    "a total is to be split into the lines it is made of; funnel for stage drop-off; else lookup, trend, "
    "breakdown or other.\n"
    "- measures: every measure asked for, one entry each, in the user's words with abbreviations spelled "
    "out (words), plus your best metric id from the catalogue listing when you are sure (else empty). "
    "Never split a measure into variants the user did not name and never add a qualifier they did not "
    "say; the catalogue knows the default meaning. When the message continues the previous turn and "
    "names no measure, carry the previous turn's.\n"
    "- entity_dimension and entity_phrases: the dimension id (from the listing) holding the named "
    "entities, and each entity as the user wrote it.\n"
    "- rank_by, rank_order, top_n: the measure that makes an entity best (what it returned, not what it "
    "cost), asc when the worst are wanted, and the count when one is given.\n"
    "- breakdown_dimensions and breakdown_words: dimension ids for every 'by ...' or 'per ...' the user "
    "asks for, and the user's words for them. A word that only says where named entities live is not a "
    "breakdown.\n"
    "- exclusion_phrases: every value the user asks to leave out ('exclude ...', 'without ...', 'other "
    "than ...'), as written.\n"
    "- ordinary_words: of the listed data-value words, every one the message does not mean as that "
    "dimension's value (ordinary language, or meant for a different kind of thing).\n"
    "- windows: every period named, as expressions, never dates: name for named periods, unit + n + "
    "ending for last-N periods, start_iso/end_iso only for dates the user writes. A baseline the user "
    "asks for ('against the days before') uses ending=before_event. Do not invent a window the user did "
    "not ask for; names_period is true when any period is named.\n"
    "- grain, direction, through_hour, part_of_whole: only when the user states them.\n"
    "- needs_clarification: when the question cannot be answered without one answer from the user (two "
    "measures fit equally, a named value fits several dimensions equally, a required period is missing), "
    "state that one question; else null."
)


def build_prompt(
    query: str,
    *,
    read_model: SemanticReadModel,
    value_words: list[str],
    value_meanings: Mapping[str, str] | None = None,
    prior_question: str = "",
    prior_offer: str = "",
    exemplars: list[dict[str, Any]] | None = None,
) -> str:
    """Build the understand prompt from the read model and context."""
    parts = [f"Message: {query}"]

    if prior_question or prior_offer:
        prior = ["Previous turn:"]
        if prior_question:
            prior.append(f"- the user asked: {prior_question[:300]}")
        if prior_offer:
            prior.append(f"- the answer ended by offering: {prior_offer[:300]}")
        parts.append("\n".join(prior))

    if value_words:
        meanings = value_meanings or {}
        parts.append(
            "Data-value words (words in the message the data records as values, "
            "and where they are recorded):\n"
            + "\n".join(
                f"- {w}" + (f": recorded as {meanings[w]}" if meanings.get(w) else "")
                for w in value_words
            )
        )

    # Catalogue concept labels, dimension labels, hierarchy levels — NO metric ids
    parts.append(_render_catalogue_for_prompt(read_model))

    if exemplars:
        parts.append(
            "Verified examples (question -> draft structure):\n"
            + "\n".join(
                f"- Q: {ex.get('question', '')}\n  kind: {ex.get('kind', '')}, shape: {ex.get('shape', '')}, "
                f"measures: {[m.get('phrase', '') for m in ex.get('measures', [])]}"
                for ex in exemplars[:3]
            )
        )

    parts.append("Return QuestionDraft now.")
    return "\n\n".join(parts)


def _render_catalogue_for_prompt(read_model: SemanticReadModel) -> str:
    """Render catalogue for the understand prompt: labels and dimension names only.

    No metric ids, no channel words, no concept words that could bias the model.
    Only the human-readable labels and dimension names the user would use.
    """
    lines = ["Catalogue vocabulary (use these labels and dimension names in your draft):"]

    # Metric labels (human-readable names)
    metric_labels = []
    for metric in read_model.metrics:
        label = metric.label or metric.id
        if label:
            metric_labels.append(f"- {label}")
    if metric_labels:
        lines.append("Measures (labels only):")
        lines.extend(metric_labels)

    # Dimension labels
    dim_labels = []
    for dim in read_model.dimensions:
        label = dim.id
        if dim.extra and dim.extra.get("label"):
            label = dim.extra["label"]
        dim_labels.append(f"- {label}")
    if dim_labels:
        lines.append("Dimensions:")
        lines.extend(dim_labels)

    # Hierarchy levels
    hier_labels = []
    for hier in read_model.hierarchies:
        if hier.levels:
            hier_labels.append(f"- {hier.id}: {', '.join(hier.levels)}")
    if hier_labels:
        lines.append("Hierarchy levels:")
        lines.extend(hier_labels)

    return "\n".join(lines)