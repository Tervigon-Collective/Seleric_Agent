"""Prompt builder for the understand LLM call (M1.1).

Builds the prompt ONLY from the read model: concept labels, dimension labels,
hierarchy level names, and value-candidate words. NO metric or channel words
in the prompt text (Rule A2).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from seleric_swarm.semantic import SemanticReadModel

# Token budget for the understand prompt: the catalogue holds ~228 measure labels
# and the full dump dominates the prompt. Only measures whose label shares a word
# with the message are named; the rest are left to concept search (mission
# ``resolve_measures``), which resolves a measure the draft names in ``words``
# even when ``metric_id`` is empty. A small stable fallback keeps the list usable
# when the message names no measure at all.
_MAX_MEASURE_LABELS = 80
_MEASURE_FALLBACK = 30

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
    "out (words), plus in metric_id the measure's label exactly as the catalogue listing shows it when you are sure (else empty). "
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
    "state that one question; else null. Do not ask when one reading is the usual one (a plain measure name means its listed default, e.g. the un-prefixed one) or when the user only asks how things compare or performed: draft it with the measures you would show."
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
    """Build the understand prompt from the read model and context.

    The stable, query-independent part of the catalogue (dimensions and hierarchy
    levels) leads the prompt so it forms a reusable prefix for provider prompt
    caching; the message and the query-ranked measures follow.
    """
    parts = [_render_catalogue_for_prompt(read_model, query=query, prior_question=prior_question)]

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

    if prior_question or prior_offer:
        prior = ["Previous turn:"]
        if prior_question:
            prior.append(f"- the user asked: {prior_question[:300]}")
        if prior_offer:
            prior.append(f"- the answer ended by offering: {prior_offer[:300]}")
        parts.append("\n".join(prior))

    parts.append(f"Message: {query}")

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


def _words(text: str) -> list[str]:
    """Lowercase alphanumeric words. No regex: the ``understand`` layer forbids ``re`` (Rule A2)."""
    cleaned = "".join(ch if ch.isalnum() else " " for ch in text.casefold())
    return cleaned.split()


def _measure_labels(read_model: SemanticReadModel, query: str, prior_question: str) -> list[str]:
    """Measure labels whose words overlap the message, topped up to a small floor.

    Only the read model's own labels and the user's words are used (Rule A2); no
    metric or channel literal lives here.
    """
    words = {w for w in _words(f"{query} {prior_question}") if len(w) >= 3}
    matched: list[str] = []
    rest: list[str] = []
    for metric in read_model.metrics:
        label = (metric.label or metric.id).strip()
        if not label:
            continue
        label_words = set(_words(label))
        (matched if words & label_words else rest).append(label)
    picked: list[str] = []
    seen: set[str] = set()
    for label in matched:
        if label not in seen:
            seen.add(label)
            picked.append(label)
    if len(picked) < _MEASURE_FALLBACK:
        for label in rest:
            if label not in seen:
                seen.add(label)
                picked.append(label)
            if len(picked) >= _MEASURE_FALLBACK:
                break
    return picked[:_MAX_MEASURE_LABELS]


def _render_catalogue_for_prompt(
    read_model: SemanticReadModel, *, query: str = "", prior_question: str = ""
) -> str:
    """Render catalogue for the understand prompt: labels and dimension names only.

    No metric ids, no channel words, no concept words that could bias the model.
    Only the human-readable labels and dimension names the user would use.

    Dimensions and hierarchy levels come first (identical across every mission, so
    they form a cacheable prompt prefix); measures follow, bounded to the labels
    relevant to this message.
    """
    lines = ["Catalogue vocabulary (use these labels and dimension names in your draft):"]

    # Dimension labels (query-independent: kept whole and first for prompt caching)
    dim_labels = []
    for dim in read_model.dimensions:
        label = dim.id
        if dim.extra and dim.extra.get("label"):
            label = dim.extra["label"]
        dim_labels.append(f"- {label}")
    if dim_labels:
        lines.append("Dimensions:")
        lines.extend(dim_labels)

    # Hierarchy levels (query-independent)
    hier_labels = []
    for hier in read_model.hierarchies:
        if hier.levels:
            hier_labels.append(f"- {hier.id}: {', '.join(hier.levels)}")
    if hier_labels:
        lines.append("Hierarchy levels:")
        lines.extend(hier_labels)

    # Measure labels (query-ranked, bounded)
    metric_labels = [f"- {label}" for label in _measure_labels(read_model, query, prior_question)]
    if metric_labels:
        lines.append("Measures (labels only):")
        lines.extend(metric_labels)

    return "\n".join(lines)
