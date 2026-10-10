"""Prompt builder for the understand LLM call (M1.1).

Builds the prompt ONLY from the read model: concept labels, dimension labels,
hierarchy level names, and value-candidate words. NO metric or channel words
in the prompt text (Rule A2).
"""

from __future__ import annotations

from typing import Any, Mapping

from seleric_swarm.semantic import SemanticReadModel


_INSTRUCTIONS = (
    "Read the user's message to a business-analytics assistant and fill QuestionDraft. "
    "Do not answer analytics questions and do not invent numbers.\n"
    "- kind: conversation when the message is a greeting, thanks, small talk, or a "
    "question about the assistant — no business data asked. overview: how the business "
    "as a whole is doing, no specific metric named. forecast: a future value. "
    "what_if: a hypothetical scenario. action: create, change, pause, launch, or "
    "delete something. exploration: open-ended 'what else' or 'show me'. "
    "analysis: everything else.\n"
    "- shape: the question's shape from the plan template registry (lookup, trend, "
    "breakdown, period_comparison, entity_comparison, composition, funnel, why_single_metric, "
    "other). Use 'other' for conversation.\n"
    "- measures: every measure the user asks for, one entry each, in their exact "
    "words with abbreviations spelled out. Include your best catalogue metric id if "
    "you are confident; else leave metric_id empty. Never add a qualifier the user "
    "did not say (date basis, channel, gross/net).\n"
    "- filter_phrases: every value the user names as a filter ('on Meta', "
    "'channel is Google'), quoted from the message.\n"
    "- breakdown_phrases: every 'by ...' or 'per ...' or 'split by ...' the user "
    "asks for, quoted.\n"
    "- exclusion_phrases: every 'exclude ...', 'without ...', 'not ...' the user "
    "says, quoted.\n"
    "- entity_phrases: every named entity the user mentions ('campaign X', "
    "'product Y', 'SKU Z'), quoted.\n"
    "- windows: every time period the user names as an expression — not dates. "
    "Include role (event/baseline/context) and the user's words ('yesterday', "
    "'last 7 days', 'this month', 'the 3 days before').\n"
    "- grain: the time bucket if the user names one ('daily', 'weekly', "
    "'monthly'), else null.\n"
    "- ranking: the user's words for ranking ('top 5 by ROAS', 'worst 3 by spend'), "
    "else null.\n"
    "- ranking_order: 'desc' for best/top/highest, 'asc' for worst/lowest.\n"
    "- ranking_limit: the number if the user says one.\n"
    "- follows_prior: true when the question only makes sense with the previous "
    "turn ('it', 'those', 'same for X', 'why').\n"
    "- accepts_offer: true when the user accepts the offer that ended the previous "
    "answer ('yes', 'sure', 'go ahead').\n"
    "- restyles_prior: true when the message only asks to see the previous answer "
    "in another form (as a graph, chart, table, shorter, in lakhs) and names no "
    "new measure, entity, or period.\n"
    "- ordinary_words: of the listed data-value words, the ones the message uses "
    "as plain language ('other days', 'direct answer'), not as that dimension "
    "value.\n"
    "- needs_clarification: if a required axis is missing, a named value matches "
    "several dimensions with comparable volume, or resolve_concept would return a "
    "disambiguation — state the one question to ask the user. Otherwise null."
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