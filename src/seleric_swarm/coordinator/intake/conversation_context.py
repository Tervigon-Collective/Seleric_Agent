"""Deterministic follow-up context from a conversation ContextBundle.

The conversation layer already builds and persists ``context_bundle`` (recent
messages, memories, artifacts) on every submit. Classification and lookup
must read it: a follow-up like "gross sale" after "gross sales today" is not
a brand-new question.

Time inheritance is a tokenizer over prior user text — the same
``window_from_query`` used for the current turn — not an LLM rewrite of the
query. The stored user query is left unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.services.time_range import window_from_query

_TIME_STRIP = re.compile(
    r"\b(?:yesterday|today|tomorrow|"
    r"this\s+(?:week|month|year)|"
    r"last\s+\d+\s+(?:days?|weeks?|months?|quarters?)|"
    r"last\s+(?:week|month|year|quarter)|"
    r"compare|versus|vs\.?|"
    r"(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|"
    r"oct|nov|dec)\s+\d{4})\b|"
    r"\b20\d{2}-\d{2}-\d{2}\b",
    re.IGNORECASE,
)
_COMPARISON = re.compile(r"\b(?:compare|versus|vs\.?)\b", re.IGNORECASE)
_STOP = {
    "and", "the", "a", "an", "for", "all", "that", "thats", "this", "it",
    "you", "your", "me", "my", "we", "our", "s", "re", "ve", "ll", "d",
}
_SOCIAL = {
    "thanks", "thank", "thx", "ty", "please", "ok", "okay", "k", "yes", "no",
    "hi", "hello", "hey", "yo", "sup", "bye", "goodbye", "cool", "great",
    "awesome", "nice", "good", "got", "appreciate", "cheers", "welcome",
    "sure", "yup", "yeah", "yep", "nah", "morning", "afternoon", "evening",
}
_FOLLOWUP = {
    "same", "thing", "those", "these", "one", "too", "also", "again",
    "what", "about", "how", "much", "was", "were", "is", "are", "did",
    "does", "do", "of", "on", "in", "to", "just", "only", "still",
    "number", "value", "figure",
}
_CHATTER = _STOP | _SOCIAL


def recent_user_texts(context_bundle: Mapping[str, Any] | None) -> list[str]:
    """User TEXT parts from newest-last recent_messages, oldest first."""
    if not context_bundle:
        return []
    messages = context_bundle.get("recent_messages")
    if not isinstance(messages, list):
        return []
    texts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "").upper() != "USER":
            continue
        parts = message.get("parts")
        if not isinstance(parts, list):
            continue
        chunks = [
            part["content"].strip()
            for part in parts
            if isinstance(part, dict)
            and str(part.get("type") or "").upper() == "TEXT"
            and isinstance(part.get("content"), str)
            and part["content"].strip()
        ]
        if chunks:
            texts.append("\n".join(chunks))
    return texts


def leftover_tokens(text: str) -> list[str]:
    stripped = _TIME_STRIP.sub(" ", text)
    ignore = _CHATTER | _FOLLOWUP
    return [
        token
        for token in re.findall(r"[a-z0-9]+", stripped.casefold())
        if token not in ignore
    ]


def is_incidental_time_chatter(text: str, timezone: str, as_of: str | None) -> bool:
    """True when a date word sits inside small talk, not a metric/time ask."""
    if window_from_query(text, timezone, as_of) is None:
        return False
    if leftover_tokens(text):
        return False
    raw = set(re.findall(r"[a-z0-9]+", text.casefold()))
    return bool(raw & _SOCIAL)


def inherit_time_range(
    query: str,
    *,
    timezone: str,
    as_of: str | None,
    context_bundle: Mapping[str, Any] | None,
) -> TimeRangeV1 | None:
    """Prior-turn window when the current query names no time of its own.

    Returns ``None`` when the current query already has an explicit window
    (so "gross sale yesterday" after "today" keeps yesterday) or when no
    prior user message carried a parseable business window.
    """
    if window_from_query(query, timezone, as_of) is not None:
        return None
    allow_comparison = bool(_COMPARISON.search(query or ""))
    for text in reversed(recent_user_texts(context_bundle)):
        if is_incidental_time_chatter(text, timezone, as_of):
            continue
        window = window_from_query(text, timezone, as_of)
        if window is None:
            continue
        if window.kind == "comparison" and not allow_comparison:
            continue
        return window
    return None


def inherit_metric_source(
    query: str,
    *,
    context_bundle: Mapping[str, Any] | None,
    timezone: str,
    as_of: str | None,
) -> str | None:
    """Most recent prior user ask that still names a business metric."""
    current = " ".join((query or "").casefold().split())
    for text in reversed(recent_user_texts(context_bundle)):
        if " ".join(text.casefold().split()) == current:
            continue
        if is_incidental_time_chatter(text, timezone, as_of):
            continue
        if leftover_tokens(text):
            return text
    return None


def context_fingerprint(context_bundle: Mapping[str, Any] | None) -> str:
    prior = recent_user_texts(context_bundle)
    if not prior:
        return ""
    return " ".join(prior[-4:]).casefold()[:240]


def cache_fingerprint(window: TimeRangeV1 | None) -> str:
    if window is None:
        return ""
    return "|".join(
        [
            window.kind,
            window.start or "",
            window.end or "",
            window.start_b or "",
            window.end_b or "",
            window.relative_token or "",
        ]
    )


def format_prior_turns(context_bundle: Mapping[str, Any] | None, *, limit: int = 4) -> str:
    lines = []
    for text in recent_user_texts(context_bundle)[-limit:]:
        compact = " ".join(text.split())
        if compact:
            lines.append(f"- {compact[:200]}")
    return "\n".join(lines)
