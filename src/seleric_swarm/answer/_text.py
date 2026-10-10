"""Placeholder scanning by characters: ``{claim_id}`` tokens and digits outside them."""

from __future__ import annotations

from collections.abc import Callable


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
