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
from typing import Any, Literal

import httpx

_log = logging.getLogger("seleric.agent.intent")

ComplexityLabel = Literal["simple", "moderate", "complex"]

IntentLabel = Literal[
    "lookup",
    "aggregation",
    "comparison",
    "trend",
    "diagnostic",
    "forecast",
    "simulation",
    "causal_investigation",
]

_LABELS: frozenset[str] = frozenset(
    {
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

_CRITERIA = {
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


@dataclass(frozen=True)
class QueryClassification:
    """Parallel Jev judgments about one query. Every field is independently
    fail-open — a missing/garbled answer for one question leaves that field
    ``None`` without affecting the others."""

    intent: IntentLabel | None = None
    complexity: ComplexityLabel | None = None
    needs_write: bool | None = None


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


def _normalize_complexity(value: Any) -> ComplexityLabel | None:
    """Jev's `score` is a float in [0, 2]; round to the nearest ordinal level.
    Also accepts an int/str index or a label directly."""
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        return None
    if isinstance(value, (int, float)):
        idx = min(len(_COMPLEXITY_LEVELS) - 1, max(0, round(value)))
        return _COMPLEXITY_LEVELS[idx]
    labels = {label: label for label in _COMPLEXITY_LEVELS}
    digits = {str(i): _COMPLEXITY_LEVELS[i] for i in range(len(_COMPLEXITY_LEVELS))}
    key = value.strip().lower() if isinstance(value, str) else value
    return labels.get(key) or digits.get(key)


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
    intent = _answer_value(answers, "intent")
    return QueryClassification(
        intent=intent if intent in _LABELS else None,
        complexity=_normalize_complexity(_answer_value(answers, "complexity")),
        needs_write=_normalize_bool(_answer_value(answers, "needs_write")),
    )


async def classify_query(
    query: str,
    *,
    base_url: str,
    api_key: str,
    timeout: float = 1.0,
) -> QueryClassification:
    """One Jev call, three parallel judgments: intent, complexity, needs_write."""
    if not base_url or not api_key:
        return QueryClassification()
    payload = {
        "model": "openjev",
        "state": query,
        "questions": {
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
        },
    }
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
        _log.warning("jev_classify_failed", exc_info=True)
        return QueryClassification()

    return _parse_answers(answers)


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
