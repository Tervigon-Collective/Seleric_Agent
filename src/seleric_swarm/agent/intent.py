"""Query classification labels and the Jev decisions the tools still use.

``QueryClassification`` holds the per-query routing signals. Since 2026-10-07
they come from the one structured understand call (``agent/understand.py``),
not from a Jev round trip: Jev answered in ~6s and its intent labels were close
to random live. Jev (``openjev``, a fast typed-decision service) is still used
inside tools for typed judgments over text — a write action's blast radius
(``classify_action_risk``) and a knowledge passage's relevance
(``judge_relevance``). Both fail open to ``None``.

Chart forms are deliberately *not* decided here. The vocabulary lives in
``analytics/chart_vocabulary.py`` and is shared with the UI, so a form is the
caller's explicit choice or a structural default from the evidence — never the
output of a prompt.
"""

from __future__ import annotations

import logging
import re
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
    "advisory",
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


# A time bucket the user states in words. Jev's grain guess is noise (it labels
# nearly every message the same way), so a stated bucket always wins:
# "gross revenue last week all days" came back as one weekly total (live
# 2026-10-05) and the user then asked about a single day of it.
_STATED_GRAIN = (
    ("day", re.compile(
        r"\b(daily|day[- ]?(?:wise|by[- ]day|on[- ]day|level)|per[- ]day|each day|every day|"
        r"all(?: the)? days|by (?:the )?day|for each day)\b", re.IGNORECASE)),
    ("week", re.compile(
        r"\b(weekly|week[- ]?(?:wise|by[- ]week|on[- ]week|level)|per[- ]week|each week|every week|"
        r"all(?: the)? weeks|by (?:the )?week)\b", re.IGNORECASE)),
    ("month", re.compile(
        r"\b(monthly|month[- ]?(?:wise|by[- ]month|on[- ]month|level)|per[- ]month|each month|"
        r"every month|all(?: the)? months|by (?:the )?month)\b", re.IGNORECASE)),
)


def stated_grain(query: str) -> str | None:
    """The time bucket the question names in words, or None. Ambiguous (two
    buckets named) -> None, leaving the choice to the agent."""
    found = {grain for grain, pattern in _STATED_GRAIN if pattern.search(query or "")}
    return found.pop() if len(found) == 1 else None


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
    # Accept either the host ("https://jevmodel.org") or the full endpoint
    # ("https://jevmodel.org/v1/systemone") as JEV_BASE_URL — don't double the
    # path when it's already present.
    root = base_url.rstrip("/")
    url = root if root.endswith("/v1/systemone") else f"{root}/v1/systemone"
    payload = {"model": "openjev", "state": state, "questions": questions}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        response.raise_for_status()
        answers = response.json().get("answers")
    except Exception:
        _log.warning("jev_call_failed", exc_info=True)
        return None
    return answers if isinstance(answers, dict) else None


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
