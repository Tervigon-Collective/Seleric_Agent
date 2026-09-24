"""Jev-based query classification — routing hints only, no LLM call.

Jev (``openjev``) is a fast typed-decision service: it judges unstructured
text with parallel ``choice``/``score``/``noul`` questions in one round trip.
It never generates values (per its docs: "Do not ask Jev to invent a value, a
date, or a dollar amount") — every judgment picks from criteria we supply.

We already pay one mandatory Jev hop per mission for ``intent``; ``classify_query``
asks ``intent`` + ``complexity`` + ``needs_write`` in the *same* call (they run
in parallel server-side), so the extra signals cost no extra latency.

Fails open: any timeout/HTTP/parse error yields an empty classification so a
classifier outage never blocks or fails a mission. Callers fall back to the
full ``build_seleric_agent()`` loop, same as an alias miss in ``runner.py``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

import httpx

_log = logging.getLogger("seleric.agent.intent")

ComplexityLabel = Literal["simple", "moderate", "complex"]

IntentLabel = Literal[
    "conversation",
    "lookup",
    "aggregation",
    "comparison",
    "trend",
    "diagnostic",
    "forecast",
    "simulation",
    "causal_investigation",
]

# Analytics grain (#1) — the time bucket a comparison/series is computed at.
GrainLabel = Literal["day", "week", "month", "none"]

# Time window (#1 extra) — how the query names its period. Jev classifies the
# *kind* of window; it never emits dates. ``custom_date_range`` means the user
# gave explicit start/end dates (the agent extracts them, not Jev).
PeriodLabel = Literal[
    "today",
    "yesterday",
    "last_7d",
    "last_30d",
    "this_month",
    "last_month",
    "this_quarter",
    "this_year",
    "custom_date_range",
    "none",
]

# Anomaly / movement direction the user cares about (#3).
DirectionLabel = Literal["increase", "decrease", "either"]

# Write-action blast radius (#4).
RiskLabel = Literal["low", "medium", "high"]

_LABELS: frozenset[str] = frozenset(
    {
        "conversation",
        "lookup",
        "aggregation",
        "comparison",
        "trend",
        "diagnostic",
        "forecast",
        "simulation",
        "causal_investigation",
    }
)
_GRAIN_LABELS: frozenset[str] = frozenset({"day", "week", "month", "none"})
_PERIOD_LABELS: frozenset[str] = frozenset(
    {
        "today",
        "yesterday",
        "last_7d",
        "last_30d",
        "this_month",
        "last_month",
        "this_quarter",
        "this_year",
        "custom_date_range",
        "none",
    }
)
_DIRECTION_LABELS: frozenset[str] = frozenset({"increase", "decrease", "either"})

_CRITERIA = {
    "conversation": (
        "Greeting, thanks, acknowledgement or small talk (hi, thanks, ok, bye), "
        "or a question about the assistant itself — asks for no business data or metric"
    ),
    "lookup": "Deterministic single-value fetch — one metric, one period",
    "aggregation": "Group/sum/rank across a dimension (e.g. top N, total by category)",
    "comparison": "Two or more periods, segments, or entities set against each other",
    "trend": "A metric's movement over a time series",
    "diagnostic": "Why a metric moved — anomaly explanation",
    "forecast": "A future value projection",
    "simulation": "A hypothetical / what-if scenario",
    "causal_investigation": "A causal claim requiring refutation/sensitivity checks",
}

# score criteria is an ordinal ARRAY, low → high; Jev returns a float `score`
# in [0, len-1] plus a `legend`/`probabilities`/`confidence` block.
_COMPLEXITY_CRITERIA = [
    "A single fetch answers it — one metric, one period, no reasoning",
    "A few dependent steps — a fetch then one calculation or comparison",
    "Multi-step investigation — several fetches, analysis, or causal work",
]
_COMPLEXITY_LEVELS: tuple[ComplexityLabel, ...] = ("simple", "moderate", "complex")

# noul criteria is a true/false record; Jev returns `noul` = P(true) in [0, 1].
_WRITE_CRITERIA = {
    "true": "Asks to create, change, pause, launch, or delete something (a write/action)",
    "false": "Only reads or analyses data — no change to any resource",
}

_GRAIN_CRITERIA = {
    "day": "Day-by-day — daily values, a single day, or a day-over-day comparison",
    "week": "Weekly buckets",
    "month": "Monthly buckets",
    "none": "No time bucketing — a single total or a non-time-series answer",
}

_PERIOD_CRITERIA = {
    "today": "Only today / so far today",
    "yesterday": "Only yesterday",
    "last_7d": "A rolling week (last 7 days)",
    "last_30d": "A rolling month (last 30 days)",
    "this_month": "The current calendar month to date",
    "last_month": "The previous full calendar month",
    "this_quarter": "The current calendar quarter to date",
    "this_year": "The current calendar year to date",
    "custom_date_range": "The user named explicit start and/or end dates",
    "none": "No time window mentioned",
}

_DIRECTION_CRITERIA = {
    "increase": "About a rise / growth / why something went up",
    "decrease": "About a fall / drop / why something went down",
    "either": "Direction not specified — any movement",
}

# depends_on_prior (#2): an elliptical follow-up that only makes sense against
# the previous turn — "and for brand X?", "what about last month?", "why?".
_FOLLOWUP_CRITERIA = {
    "true": "An incomplete follow-up that reuses the previous turn's metric, period, or entity",
    "false": "A self-contained question that stands on its own",
}

# score criteria for write-action blast radius (#4), low → high.
_RISK_CRITERIA = [
    "Reversible and small — a single low-budget change",
    "Moderate — changes a live campaign's budget or on/off status",
    "High — bulk change, large budget, or hard to reverse (delete/launch)",
]
_RISK_LEVELS: tuple[RiskLabel, ...] = ("low", "medium", "high")

# noul criteria for knowledge-hit relevance (#5).
_RELEVANCE_CRITERIA = {
    "true": "The passage directly helps answer the question",
    "false": "Off-topic, or only incidentally mentions the query's words",
}


@dataclass(frozen=True)
class QueryClassification:
    """Parallel Jev judgments about one query. Every field is independently
    fail-open — a missing/garbled answer for one question leaves that field
    ``None`` without affecting the others."""

    intent: IntentLabel | None = None
    complexity: ComplexityLabel | None = None
    needs_write: bool | None = None
    grain: GrainLabel | None = None
    period: PeriodLabel | None = None
    direction: DirectionLabel | None = None
    depends_on_prior: bool | None = None


def _answer_value(answers: dict[str, Any], key: str) -> Any:
    """Pull the scalar judgment for *key* tolerating an undocumented response
    shape — Jev's answer node carries the value under a type-named field
    (``choice`` is the one confirmed on the wire; the rest are best-effort)."""
    node = answers.get(key)
    if not isinstance(node, dict):
        return None
    for field_name in ("choice", "score", "noul", "answer", "value", "label"):
        if field_name in node:
            return node[field_name]
    return None


_L = TypeVar("_L", bound=str)


def _normalize_ordinal(value: Any, levels: tuple[_L, ...]) -> _L | None:
    """Jev's `score` is a float over an ordinal array; round to the nearest
    level. Also accepts an int/str index or a label directly."""
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        return None
    if isinstance(value, (int, float)):
        return levels[min(len(levels) - 1, max(0, round(value)))]
    labels: dict[Any, _L] = {label: label for label in levels}
    digits: dict[Any, _L] = {str(i): levels[i] for i in range(len(levels))}
    key = value.strip().lower() if isinstance(value, str) else value
    return labels.get(key) or digits.get(key)


def _choice(answers: dict[str, Any], key: str, labels: frozenset[str]) -> Any:
    """A Jev ``choice`` answer, kept only if it is one of the labels we offered."""
    value = _answer_value(answers, key)
    return value if value in labels else None


def _normalize_bool(value: Any) -> bool | None:
    """Jev's `noul` is P(true) in [0, 1]; ≥0.5 is True. Also accepts a bool or
    a yes/no/true/false/1/0 token."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value >= 0.5
    token = str(value).strip().lower()
    if token in {"yes", "true", "1"}:
        return True
    if token in {"no", "false", "0"}:
        return False
    return None


def _parse_answers(answers: Any) -> QueryClassification:
    """Map a Jev ``answers`` block to a QueryClassification, per-field fail-open."""
    if not isinstance(answers, dict):
        return QueryClassification()
    return QueryClassification(
        intent=_choice(answers, "intent", _LABELS),
        complexity=_normalize_ordinal(_answer_value(answers, "complexity"), _COMPLEXITY_LEVELS),
        needs_write=_normalize_bool(_answer_value(answers, "needs_write")),
        grain=_choice(answers, "grain", _GRAIN_LABELS),
        period=_choice(answers, "period", _PERIOD_LABELS),
        direction=_choice(answers, "direction", _DIRECTION_LABELS),
        depends_on_prior=_normalize_bool(_answer_value(answers, "depends_on_prior")),
    )


async def _post_jev(
    state: str,
    questions: dict[str, Any],
    *,
    base_url: str,
    api_key: str,
    timeout: float,
) -> dict[str, Any] | None:
    """One Jev round trip. Returns the ``answers`` block, or ``None`` on any
    error / missing config — every caller is fail-open on ``None``."""
    if not base_url or not api_key:
        return None
    payload = {"model": "openjev", "state": state, "questions": questions}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/v1/systemone",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        response.raise_for_status()
        answers = response.json().get("answers")
    except Exception:
        _log.warning("jev_call_failed", exc_info=True)
        return None
    return answers if isinstance(answers, dict) else None


async def classify_query(
    query: str,
    *,
    base_url: str,
    api_key: str,
    timeout: float = 1.0,
) -> QueryClassification:
    """One Jev call, all per-query routing judgments in parallel: intent,
    complexity, needs_write, grain, period, direction, depends_on_prior."""
    answers = await _post_jev(
        query,
        {
            "intent": {
                "type": "choice",
                "instructions": "Classify the analytical intent of this query.",
                "criteria": _CRITERIA,
            },
            "complexity": {
                "type": "score",
                "instructions": "How much multi-step reasoning does answering this need?",
                "criteria": _COMPLEXITY_CRITERIA,
            },
            "needs_write": {
                "type": "noul",
                "instructions": "Does the user ask to change a resource, not just read data?",
                "criteria": _WRITE_CRITERIA,
            },
            "grain": {
                "type": "choice",
                "instructions": "At what time bucket should this be computed?",
                "criteria": _GRAIN_CRITERIA,
            },
            "period": {
                "type": "choice",
                "instructions": "What time window does the query ask for? Do NOT infer dates.",
                "criteria": _PERIOD_CRITERIA,
            },
            "direction": {
                "type": "choice",
                "instructions": "Which direction of movement is the user asking about?",
                "criteria": _DIRECTION_CRITERIA,
            },
            "depends_on_prior": {
                "type": "noul",
                "instructions": "Is this an incomplete follow-up that reuses the previous turn?",
                "criteria": _FOLLOWUP_CRITERIA,
            },
        },
        base_url=base_url,
        api_key=api_key,
        timeout=timeout,
    )
    return _parse_answers(answers)


async def classify_action_risk(
    action_summary: str,
    *,
    base_url: str,
    api_key: str,
    timeout: float = 1.0,
) -> RiskLabel | None:
    """#4: blast-radius tier for a proposed write action. Fail-open (``None``)."""
    answers = await _post_jev(
        action_summary,
        {
            "risk": {
                "type": "score",
                "instructions": "How large and hard-to-reverse is this write action?",
                "criteria": _RISK_CRITERIA,
            }
        },
        base_url=base_url,
        api_key=api_key,
        timeout=timeout,
    )
    if not answers:
        return None
    return _normalize_ordinal(_answer_value(answers, "risk"), _RISK_LEVELS)


async def judge_relevance(
    query: str,
    passage: str,
    *,
    base_url: str,
    api_key: str,
    timeout: float = 1.0,
) -> bool | None:
    """#5: does ``passage`` actually help answer ``query``? Fail-open (``None``)."""
    state = f"QUESTION: {query}\n\nPASSAGE: {passage}"
    answers = await _post_jev(
        state,
        {
            "relevant": {
                "type": "noul",
                "instructions": "Does the passage directly help answer the question?",
                "criteria": _RELEVANCE_CRITERIA,
            }
        },
        base_url=base_url,
        api_key=api_key,
        timeout=timeout,
    )
    if not answers:
        return None
    return _normalize_bool(_answer_value(answers, "relevant"))


async def classify_intent(
    query: str,
    *,
    base_url: str,
    api_key: str,
    timeout: float = 1.0,
) -> IntentLabel | None:
    """Backwards-compatible intent-only accessor (delegates to classify_query)."""
    classification = await classify_query(
        query, base_url=base_url, api_key=api_key, timeout=timeout
    )
    return classification.intent
