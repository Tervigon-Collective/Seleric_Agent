from __future__ import annotations

import re
from typing import Any

_NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?(?![A-Za-z])")
_ISO_DATE_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
_ID_RE = re.compile(r"\b(?:EV|CL|M|T)-[A-Za-z0-9]+\b")


def _normalize_number(token: str) -> str:
    return token.replace(",", "").replace("+", "")


def allowed_numbers(evidence: list[dict[str, Any]], extra: list[Any] | None = None) -> set[str]:
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
    return {token for token in allowed if token}


def extract_numbers(text: str) -> list[str]:
    cleaned = _ISO_DATE_RE.sub(" ", text)
    cleaned = _ID_RE.sub(" ", cleaned)
    return [_normalize_number(match.group(0)) for match in _NUMBER_RE.finditer(cleaned)]


def unaudited_numbers(text: str, evidence: list[dict[str, Any]], extra: list[Any] | None = None) -> list[str]:
    allowed = allowed_numbers(evidence, extra)
    leaked: list[str] = []
    for token in extract_numbers(text):
        if token in {"", "-", "."}:
            continue
        if token not in allowed:
            leaked.append(token)
    return leaked
