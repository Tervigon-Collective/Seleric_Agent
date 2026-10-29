"""Does a question ask about the brand's own data? Read from the catalogue's metric names, not from a list of words:
a question that names a catalogue metric (or its id) is answered from the tools, never from memory (plan Phase 6)."""
from __future__ import annotations

from typing import Any

_MIN_NAME = 4  # shorter names ("aov", "cac") are matched as whole words only, longer ones as a phrase inside the words


def _words(text: str) -> str:
    return " ".join("".join(c if c.isalnum() else " " for c in text.lower()).split())


def names_a_metric(text: str, catalogue: Any) -> bool:
    """True when ``text`` names a catalogue metric by its label or id (whole words, case-insensitive)."""
    hay = f" {_words(text)} "
    for m in getattr(catalogue, "metrics", ()) or ():
        for name in (getattr(m, "label", ""), getattr(m, "id", "")):
            norm = _words(str(name or ""))
            if len(norm) >= _MIN_NAME - 1 and f" {norm} " in hay:
                return True
    return False
