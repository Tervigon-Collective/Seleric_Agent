"""Deterministic mission complexity classifier (pasted spec sec. 8).

Runs *after* the LLM classification, on already-structured fields, so it never
spends an LLM call. The output drives how many tasks the DAG builder emits and
which agents the router is even allowed to consider.
"""

from __future__ import annotations

from collections.abc import Sequence

from seleric_swarm.coordinator.models import ComplexityLevel

# Word stems that signal each analytical mode, checked against a lowercased query.
_ANOMALY_HINTS = ("anomal", "spike", "unusual", "abnormal", "out of line")
_DIAGNOSTIC_HINTS = ("why", "root cause", "reason for", "caused", "driver of", "explain")
_PREDICTIVE_HINTS = ("forecast", "predict", "will happen", "next week", "projection", "trend toward")
_PRESCRIPTIVE_HINTS = ("what should", "recommend", "how do we fix", "what do we do", "action")


def _mentions(text: str, hints: Sequence[str]) -> bool:
    return any(hint in text for hint in hints)


def looks_like_diagnostic(query: str) -> bool:
    """True for why/root-cause/prescribe questions — not for ranking lookups."""
    text = (query or "").lower()
    return _mentions(text, _PRESCRIPTIVE_HINTS) or _mentions(text, _DIAGNOSTIC_HINTS)


def classify_complexity(
    *,
    query_class: str,
    query: str,
    metric_hints: Sequence[str] | None = None,
    entities: Sequence[str] | None = None,
) -> ComplexityLevel:
    """Map a classified query onto an L0-L5 complexity band.

    The V1 taxonomy only emits ``lookup`` / ``comparison`` / ``unsupported`` so the
    upper bands are reached mainly by ``unsupported`` diagnostic/prescriptive
    questions - useful for telling the user *why* a request is out of scope and
    what shape of swarm it would need.
    """

    text = (query or "").lower()
    metric_hints = list(metric_hints or [])
    entities = list(entities or [])

    if query_class == "comparison":
        base = ComplexityLevel.L2
    elif query_class == "lookup":
        base = ComplexityLevel.L1 if (len(metric_hints) > 1 or entities) else ComplexityLevel.L0
    else:  # unsupported / unknown - infer the shape the question really implies
        base = ComplexityLevel.L1

    if _mentions(text, _PRESCRIPTIVE_HINTS):
        return ComplexityLevel.L5 if len(metric_hints) > 1 else ComplexityLevel.L4
    if _mentions(text, _DIAGNOSTIC_HINTS):
        return ComplexityLevel.L5 if len(metric_hints) > 1 else ComplexityLevel.L4
    if _mentions(text, _PREDICTIVE_HINTS):
        return ComplexityLevel.L3
    if _mentions(text, _ANOMALY_HINTS):
        return max(base, ComplexityLevel.L3)
    return base
