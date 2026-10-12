"""Placeholder scanning by characters: ``{claim_id}`` tokens and digits outside them."""

from __future__ import annotations

from collections.abc import Callable

_MIN_INTRO_WORDS = 2


def split(text: str) -> list[tuple[bool, str]]:
    """``[(is_placeholder, chunk)]``; an unclosed brace is plain text."""
    out: list[tuple[bool, str]] = []
    i = 0
    while i < len(text):
        start = text.find("{", i)
        end = text.find("}", start + 1) if start >= 0 else -1
        if start < 0 or end < 0:
            out.append((False, text[i:]))
            break
        if start > i:
            out.append((False, text[i:start]))
        out.append((True, text[start + 1 : end].strip()))
        i = end + 1
    return out


def placeholders(text: str) -> list[str]:
    return [c for is_ph, c in split(text) if is_ph]


def stray_digits(text: str) -> bool:
    return any(not is_ph and any(ch.isdigit() for ch in c) for is_ph, c in split(text))


def fill(text: str, value_of: Callable[[str], str]) -> str:
    return "".join(value_of(c) if is_ph else c for is_ph, c in split(text))


def bare_figures(text: str) -> set[str]:
    """Claim ids whose figure is stated with no phrase of its own saying what it is.

    A clause runs from the last ``, ; :`` (or the paragraph start) to the placeholder. A
    figure with no words in its clause, or one list item after a separator with fewer than
    two ("Leaders: {c1}, {c2}"), is bare; "Gross sale rose to {c1}" is not. Characters and
    word counts only — no word list.
    """
    out: set[str] = set()
    clause = ""
    listed = False
    for is_ph, chunk in split(text):
        if not is_ph:
            for ch in ",;:":
                if ch in chunk:
                    chunk = chunk[chunk.rfind(ch) + 1 :]
                    clause = ""
                    listed = True
            clause += chunk
            continue
        words = [w for w in clause.split() if any(c.isalpha() for c in w)]
        if not words or (listed and len(words) < _MIN_INTRO_WORDS):
            out.add(chunk)
        clause = ""
        listed = False
    return out
