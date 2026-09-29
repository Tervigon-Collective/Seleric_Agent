"""Deterministic checks over ``final_response`` prose.

Two live defects motivated this module, both surviving a prompt that forbade
them in plain words (INSTRUCTIONS v0.1.24, "USER-FACING RESPONSE FORMAT"):

1. 2026-09-29: an answer led with ``INR 9,146,009`` above a table whose four
   rows summed to ``11,943,009.97``. An earlier sample led with ``₹10.59 L``
   over a table summing to ``₹105.91 L`` — the same defect, off by a scale
   step. A headline that contradicts the rows beneath it is worse than no
   headline, and no amount of prompt text reliably prevents arithmetic slips.
2. The same answer closed with ``use net_sales_all_channels ...``, leaking an
   internal metric id the contract forbids.

Both are checked here rather than asserted at the model, and both are REVISE
(retryable) signals — the revision loop in ``validation/__init__`` re-runs the
agent with the reason attached.

Deliberately NOT a provenance audit. ``services.numeric_audit.unaudited_numbers``
flags every token absent from evidence, which would also reject a *correct*
total — the response contract explicitly asks for one. This module checks the
answer against *itself*: a stated total must reconcile with the table the same
answer printed.
"""

from __future__ import annotations

import re
from itertools import combinations

_NUMBER = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_TABLE_DELIM = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")

# A claim that a number is the aggregate of the rows shown.
_TOTAL_WORD = re.compile(
    r"\b(total(?:s|led|ling)?|sum(?:med|s)?|combined|altogether|aggregate|overall)\b",
    re.IGNORECASE,
)

# Magnitude suffixes a headline may be abbreviated with. The multiplier is
# taken from the suffix the answer actually wrote — never tried speculatively.
# A blanket "any scale matches" rule would excuse the very defect this catches:
# "₹10.59 L" over a table summing to ₹105.91 L reconciles at 10^6 while being
# wrong by a factor of ten at the stated 10^5.
_SUFFIX_SCALE = {
    "k": 10**3,
    "l": 10**5,
    "lac": 10**5,
    "lakh": 10**5,
    "lakhs": 10**5,
    "m": 10**6,
    "mn": 10**6,
    "million": 10**6,
    "cr": 10**7,
    "crore": 10**7,
    "crores": 10**7,
}
_SUFFIXED = re.compile(
    r"([-+]?\d[\d,]*(?:\.\d+)?)\s*(" + "|".join(sorted(_SUFFIX_SCALE, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# Above this many rows the subset search is skipped rather than risk a slow
# validate(); a mis-stated total on a 13+ row table is left to the prompt.
_MAX_SUBSET_ROWS = 12


def _to_float(token: str) -> float | None:
    try:
        return float(token.replace(",", "").replace("+", ""))
    except ValueError:
        return None


def _numbers_in(text: str) -> list[float]:
    stripped = _ISO_DATE.sub(" ", text)
    out = []
    for match in _NUMBER.finditer(stripped):
        value = _to_float(match.group(0))
        if value is not None:
            out.append(value)
    return out


def _table_columns(text: str) -> list[list[float]]:
    """Numeric columns of every Markdown table in ``text``.

    Requires the GFM delimiter row, exactly as the UI renderer does — a block
    without one is not a table to the reader either.
    """
    lines = text.splitlines()
    columns: list[list[float]] = []
    i = 0
    while i < len(lines) - 1:
        if "|" in lines[i] and _TABLE_DELIM.match(lines[i + 1]) and "|" in lines[i + 1]:
            rows: list[list[str]] = []
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            width = max((len(r) for r in rows), default=0)
            for col in range(width):
                values = []
                for row in rows:
                    if col >= len(row):
                        continue
                    cell = _ISO_DATE.sub(" ", row[col])
                    found = _NUMBER.findall(cell)
                    # One number per cell, else it is a label, not a measure.
                    if len(found) == 1:
                        value = _to_float(found[0])
                        if value is not None:
                            values.append(value)
                if len(values) >= 2:
                    columns.append(values)
            continue
        i += 1
    return columns


def _claim_on(line: str) -> float | None:
    """The figure a total-bearing line is asserting, at its stated magnitude.

    The largest number on the line, so "9,146,009 (sum of last 3 months)" is
    read as the total and the "3" as the period count it is.
    """
    best: float | None = None
    consumed: list[tuple[int, int]] = []
    for match in _SUFFIXED.finditer(line):
        value = _to_float(match.group(1))
        if value is None:
            continue
        consumed.append(match.span())
        scaled = value * _SUFFIX_SCALE[match.group(2).lower()]
        if best is None or abs(scaled) > abs(best):
            best = scaled
    stripped = _ISO_DATE.sub(" ", line)
    for match in _NUMBER.finditer(stripped):
        if any(start <= match.start() < end for start, end in consumed):
            continue
        value = _to_float(match.group(0))
        if value is None:
            continue
        if best is None or abs(value) > abs(best):
            best = value
    return best


def _reconciles(claim: float, values: list[float]) -> bool:
    """True if ``claim`` equals the sum of some subset of ``values``.

    Subsets, not just the full sum, because "total excluding partial months"
    is a legitimate answer. Rounding-tolerant so a rounded headline counts.
    The claim arrives already at its stated magnitude, so no scale is guessed.
    """
    if not values:
        return True
    candidates = {sum(values)}
    if len(values) <= _MAX_SUBSET_ROWS:
        for size in range(1, len(values) + 1):
            for subset in combinations(values, size):
                candidates.add(sum(subset))
    target_claim = abs(claim)
    for candidate in candidates:
        target = abs(candidate)
        tolerance = max(target * 0.005, 1.0)
        if abs(target_claim - target) <= tolerance:
            return True
    return False


def total_mismatch(text: str) -> str | None:
    """A stated total that no column of the answer's own table can produce.

    Returns a reason string for the revision loop, or None when the answer is
    internally consistent (which includes: no table, or no total claimed).
    """
    columns = _table_columns(text)
    if not columns:
        return None
    for line in text.splitlines():
        if "|" in line or not _TOTAL_WORD.search(line):
            continue
        claim = _claim_on(line)
        if claim is None or claim == 0:
            continue
        if any(_reconciles(claim, column) for column in columns):
            continue
        sums = ", ".join(f"{sum(c):,.2f}" for c in columns)
        return (
            f"the answer states a total of {claim:,.2f} but the table it prints "
            f"sums to {sums} — recompute the total from the rows shown, or drop it"
        )
    return None


def leaked_metric_ids(text: str, metric_ids: set[str]) -> list[str]:
    """Internal metric ids appearing verbatim in the prose.

    Only ids with an underscore are considered: a single-word id would collide
    with ordinary business vocabulary and is not worth a false REVISE.
    """
    found = []
    for metric_id in metric_ids:
        token = metric_id.strip()
        if len(token) < 5 or "_" not in token:
            continue
        if re.search(rf"\b{re.escape(token)}\b", text):
            found.append(token)
    return sorted(found)
