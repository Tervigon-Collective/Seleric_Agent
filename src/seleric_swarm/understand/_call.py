"""LLM call for understand with retry and Clarification handling (M1.1)."""

from __future__ import annotations

import logging
import time
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from seleric_swarm.semantic import SemanticReadModel
from seleric_swarm.understand.draft import (
    Clarification,
    QuestionDraft,
    UnderstandOutput,
)

_log = logging.getLogger("seleric.understand")


async def call_understand(
    model: Model | None,
    prompt: str,
    *,
    read_model: SemanticReadModel,
) -> tuple[UnderstandOutput, dict[str, Any]]:
    """Call the understand LLM with one retry on failure.

    Returns an UnderstandOutput wrapper containing either a QuestionDraft
    or a Clarification. On model failure or validation error after retry,
    returns UnderstandOutput.from_clarification(...) with reason
    'understanding_unavailable' (failure policy A4).
    """
    if model is None or isinstance(model, TestModel):
        return (
            UnderstandOutput.from_clarification(
                Clarification(
                    reason="understanding_unavailable",
                    question_to_user=(
                        "I'm having trouble understanding your question. "
                        "Could you rephrase it?"
                    ),
                )
            ),
            {"status": "skipped", "model": "none"},
        )

    started = time.perf_counter()
    stats: dict[str, Any] = {}

    agent: Agent[None, UnderstandOutput] = Agent(
        model=model,
        output_type=UnderstandOutput,
        instructions=_INSTRUCTIONS,
        name="seleric_understand_v2",
    )

    for attempt in range(2):  # one retry = 2 attempts
        try:
            result = await agent.run(prompt)
            usage = result.usage() if callable(result.usage) else result.usage
            stats.update(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                attempt=attempt + 1,
            )
            output = result.output
            if output.is_clarification():
                stats.update(status="clarification")
                return output, stats
            qd = output.get_question_draft()
            if qd is not None:
                stats.update(status="ok", kind=qd.kind, shape=qd.shape)
            else:
                stats.update(status="ok")
            return output, stats
        except Exception as exc:
            _log.warning("understand_failed attempt=%d", attempt + 1, exc_info=True)
            if attempt == 0:
                # First attempt failed, retry
                continue
            # Second attempt failed
            stats.update(status="failed", error=type(exc).__name__)
            return (
                UnderstandOutput.from_clarification(
                    Clarification(
                        reason="understanding_unavailable",
                        question_to_user=(
                            "I'm having trouble understanding your question. "
                            "Could you rephrase it?"
                        ),
                    )
                ),
                stats,
            )

    # Should not reach here
    stats.update(status="failed", error="unexpected")
    return (
        UnderstandOutput.from_clarification(
            Clarification(
                reason="understanding_unavailable",
                question_to_user=(
                    "I'm having trouble understanding your question. "
                    "Could you rephrase it?"
                ),
            )
        ),
        stats,
    )


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