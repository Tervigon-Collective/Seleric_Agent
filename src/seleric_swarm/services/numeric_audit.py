from __future__ import annotations

import re
from typing import Any

_NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?(?![A-Za-z])")
_ISO_DATE_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")

# Internal artifact-id token (EV-/CL-/M-/T- prefix, e.g. "EV-5d68e2c96e68"),
# with an optional leading space and enclosing []/() — the single source of
# truth for this id shape. A second hand-rolled copy in orchestration/
# synthesize.py used a hex-only body ([0-9a-f]) instead of this one's
# alnum body; an id with a non-hex suffix char would strip here but not
# there, so both now import ARTIFACT_ID_RE from here instead of each
# maintaining their own pattern.
ARTIFACT_ID_RE = re.compile(r"\s*[\[(]?\b(?:EV|CL|M|T)-[A-Za-z0-9]+\b[\])]?", re.IGNORECASE)


def _normalize_number(token: str) -> str:
    return token.replace(",", "").replace("+", "")


def allowed_numbers(
    evidence: list[dict[str, Any]],
    extra: list[Any] | None = None,
    query_text: str | None = None,
) -> set[str]:
    """Build the set of numeric tokens the synthesizer is allowed to write.

    Three sources:
    1. ``extra`` — values the caller has already whitelisted (e.g. evidence
       values passed through again for convenience).
    2. Evidence rows — each row's ``value`` plus ISO date fragments from its
       ``time_range``.
    3. ``query_text`` — numbers that appear verbatim in the user's original
       question.  These are query parameters (e.g. "5" in "last 5 months"),
       not fabricated metric values, so they are allowed when the synthesizer
       echoes them back in the answer.
    """
    allowed: set[str] = set()
    for item in extra or []:
        if item is None:
            continue
        allowed.add(_normalize_number(str(item)))
    for row in evidence:
        value = row.get("value")
        if value is None:
            continue
        allowed.add(_normalize_number(str(value)))
        if isinstance(value, float) and value.is_integer():
            allowed.add(str(int(value)))
        time_range = row.get("time_range") or {}
        for key in ("start", "end"):
            if time_range.get(key):
                day = str(time_range[key])
                allowed.update(day.split("-"))
                allowed.add(day.replace("-", ""))
    if query_text:
        for match in _NUMBER_RE.finditer(query_text):
            allowed.add(_normalize_number(match.group(0)))
    return {token for token in allowed if token}


def extract_numbers(text: str) -> list[str]:
    cleaned = _ISO_DATE_RE.sub(" ", text)
    cleaned = ARTIFACT_ID_RE.sub(" ", cleaned)
    return [_normalize_number(match.group(0)) for match in _NUMBER_RE.finditer(cleaned)]


def unaudited_numbers(
    text: str,
    evidence: list[dict[str, Any]],
    extra: list[Any] | None = None,
    query_text: str | None = None,
) -> list[str]:
    """Return numeric tokens in ``text`` that are not accounted for by evidence.

    ``query_text`` (the original user question) is forwarded to
    ``allowed_numbers`` so that the synthesizer can echo query parameters
    (e.g. "5" in "last 5 months") without triggering a fabrication alarm.
    """
    allowed = allowed_numbers(evidence, extra, query_text=query_text)
    leaked: list[str] = []
    for token in extract_numbers(text):
        if token in {"", "-", "."}:
            continue
        if token not in allowed:
            leaked.append(token)
    return leaked
