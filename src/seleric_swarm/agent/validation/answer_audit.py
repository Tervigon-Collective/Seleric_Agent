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

# A claim that a number is the aggregate of the rows shown. ``total\w*`` covers
# both spellings deliberately: a live answer wrote "totaled" (one l) and an
# earlier pattern that listed only "totalled" let a wrong total straight
# through.
_TOTAL_WORD = re.compile(
    r"\b(total\w*|sums?|summed|summing|combined|altogether|aggregate|overall)\b",
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

# Up to this many rows every subset is tried. Above it an exhaustive search is
# too slow, so only the subsets a reader actually means are tried: the whole
# column, any single row, the whole column less one row ("excluding today"), and
# any run of consecutive rows ("the last 7 days"). Before 2026-10-04 a long table
# was checked against the full sum alone, so any sentence pairing a total word
# with one row's figure was flagged (live MS3-34e7eb26aa, 27 daily rows: "the
# largest single-day spend was 78,943.28 … no total is reported" → REVISE).
_MAX_SUBSET_ROWS = 12
# Consecutive-run sums are O(n²); past this many rows only the linear shapes run.
_MAX_WINDOW_ROWS = 400

# A total claim is read from its own sentence, not the whole line: a paragraph
# that mentions a total in one sentence and a peak value in the next must not
# have the peak read as the total. Splits after sentence punctuation followed by
# whitespace, so decimal points and thousands separators stay inside numbers.
# Not after ":": "Total spend: INR 9,000." split there leaves the total word
# and its figure in different pieces, so a wrong total in that (common) shape
# was never checked.
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+")

# Numbers that are never the asserted aggregate: a period length or rank
# ("30-day total", "last 7 days", "top 5 campaigns") and a percentage. Before
# 2026-10-05 the claim was the largest number in the clause, so "The 30-day
# total is ~37.6k (lowest: -42.2k …)" was read as asserting -42.2k and a
# correct answer FAILED (live MS3-701d6624a0).
_PERIOD_COUNT_AFTER = re.compile(
    r"^\s*-?\s*(?:days?|weeks?|months?|years?|quarters?|hours?|minutes?)\b", re.IGNORECASE
)
_RANK_BEFORE = re.compile(r"\b(?:last|past|next|first|top|bottom)\s*$", re.IGNORECASE)
_PERCENT_AFTER = re.compile(r"^\s*(?:%|pp\b|percent|percentage)", re.IGNORECASE)

# "overall"/"aggregate" also name a blended figure — an overall CTR or margin
# lies between the rows, it is not their sum (live MS3-c645523b51: "overall
# CTR 0.0199" over daily CTR rows was flagged as a wrong total).
_BLEND_WORDS = frozenset({"overall", "aggregate"})


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
    return [values for values, _ in _table_columns_with_rounding(text)]


def _table_columns_with_rounding(text: str) -> list[tuple[list[float], float]]:
    """Numeric columns of every Markdown table in ``text``.

    Requires the GFM delimiter row, exactly as the UI renderer does — a block
    without one is not a table to the reader either.
    """
    lines = text.splitlines()
    columns: list[tuple[list[float], float]] = []
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
                rounding = 0.0
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
                            rounding = max(rounding, _precision_tolerance(found[0], 1))
                if len(values) >= 2:
                    columns.append((values, rounding))
            continue
        i += 1
    return columns


def _precision_tolerance(token: str, scale: int) -> float:
    """Half the smallest unit the claim actually represents.

    A relative tolerance cannot work here: 0.5% of an 8-million total is 40,000,
    wide enough to accept a sum that is genuinely wrong. What a rounded figure
    legitimately hides is half its last digit — "105.91 L" hides 500, while
    "11,943,010" hides 0.5.
    """
    decimals = len(token.partition(".")[2])
    return 0.5 * (10 ** -decimals) * scale


def _claim_candidates(sentence: str) -> list[tuple[float, float, int, int]]:
    """Every figure in the sentence that could be an asserted aggregate:
    ``(value, tolerance, start, end)``. Suffixed figures ("37.6k", "10.59 L")
    carry their own scale; period counts, ranks and percentages are dropped."""
    out: list[tuple[float, float, int, int]] = []
    consumed: list[tuple[int, int]] = []
    for match in _SUFFIXED.finditer(sentence):
        value = _to_float(match.group(1))
        if value is None:
            continue
        consumed.append(match.span())
        scale = _SUFFIX_SCALE[match.group(2).lower()]
        out.append((value * scale, _precision_tolerance(match.group(1), scale), *match.span()))
    stripped = _ISO_DATE.sub(lambda m: " " * len(m.group(0)), sentence)
    for match in _NUMBER.finditer(stripped):
        if any(start <= match.start() < end for start, end in consumed):
            continue
        after, before = stripped[match.end():], stripped[: match.start()]
        if _PERIOD_COUNT_AFTER.match(after) or _PERCENT_AFTER.match(after) or _RANK_BEFORE.search(before):
            continue
        value = _to_float(match.group(0))
        if value is None:
            continue
        out.append((value, _precision_tolerance(match.group(0), 1), *match.span()))
    return out


def _claims_on(sentence: str) -> list[tuple[str, float, float]]:
    """``(total word, figure, tolerance)`` for each total word in the sentence:
    the figure written closest to that word (ties go to the one after it), so
    "9,146,009 (sum of last 3 months)" binds 9,146,009 and "the 30-day total
    is about 37.6k" binds 37.6k."""
    candidates = _claim_candidates(sentence)
    claims: list[tuple[str, float, float]] = []
    for word in _TOTAL_WORD.finditer(sentence):
        best: tuple[int, int, float, float] | None = None
        for value, tolerance, start, end in candidates:
            if end <= word.start():
                key = (word.start() - end, 1)
            elif start >= word.end():
                key = (start - word.end(), 0)
            else:
                continue
            if best is None or key < best[:2]:
                best = (*key, value, tolerance)
        if best is not None:
            claims.append((word.group(0).lower(), best[2], best[3]))
    return claims


def _reconciles(claim: float, tolerance: float, values: list[float], cell_rounding: float = 0.0) -> bool:
    """True if ``claim`` equals the sum of some subset of ``values``.

    Subsets, not just the full sum, because "total excluding partial months"
    is a legitimate answer. The claim arrives already at its stated magnitude
    with the tolerance its own rounding implies, so no scale is guessed and no
    proportional slack is granted.
    """
    if not values:
        return True
    total = sum(values)
    candidates = {total}
    if len(values) <= _MAX_SUBSET_ROWS:
        for size in range(1, len(values) + 1):
            for subset in combinations(values, size):
                candidates.add(sum(subset))
    else:
        candidates.update(values)
        candidates.update(total - v for v in values)
        if len(values) <= _MAX_WINDOW_ROWS:
            prefix = [0.0]
            for v in values:
                prefix.append(prefix[-1] + v)
            for start in range(len(values)):
                for end in range(start + 2, len(values) + 1):
                    candidates.add(prefix[end] - prefix[start])
    # Each printed cell may hide half its last digit, and the claim is usually
    # the exact sum the tool returned: 30 daily rows at 2 decimals drift by up
    # to 0.15 from it (live 2026-10-05 MS3-e18a06b408: a correct "loss of INR
    # 32,858.55 (total)" was revised away and the answer shipped without it).
    slack = max(tolerance, 0.01) + cell_rounding * len(values)
    return any(abs(abs(claim) - abs(candidate)) <= slack for candidate in candidates)


def _blend_of_rows(claim: float, tolerance: float, values: list[float]) -> bool:
    """An overall level between the rows, or an overall change between two of
    them ("overall CTR fell 0.0020")."""
    if min(values) - tolerance <= claim <= max(values) + tolerance:
        return True
    if len(values) > _MAX_WINDOW_ROWS:
        return False
    slack = max(tolerance, 1e-9)
    return any(
        abs(abs(claim) - abs(a - b)) <= slack for i, a in enumerate(values) for b in values[i + 1 :]
    )


def total_mismatch(text: str) -> str | None:
    """A stated total that no column of the answer's own table can produce.

    Returns a reason string for the revision loop, or None when the answer is
    internally consistent (which includes: no table, or no total claimed).
    """
    rounded = _table_columns_with_rounding(text)
    columns = [values for values, _ in rounded]
    if not columns:
        return None
    for line in text.splitlines():
        if "|" in line:
            continue
        for sentence in _SENTENCE_END.split(line):
            for word, claim, tolerance in _claims_on(sentence):
                if claim == 0:
                    continue
                if any(_reconciles(claim, tolerance, values, cell) for values, cell in rounded):
                    continue
                if word in _BLEND_WORDS and any(_blend_of_rows(claim, tolerance, c) for c in columns):
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


# Words a finished sentence never ends on. An answer ending on one of these
# with no closing punctuation was cut off (live 2026-10-05 MS3-29049b3e92
# shipped "I don't have a single " as a completed answer).
_DANGLING = frozenset(
    "a an the of to and or but for with in on at by from as than that which who whose "
    "my your our their its is are was were be been single each every this these those "
    "if because while when where so not no".split()
)


def cut_off(text: str) -> str | None:
    """The dangling last word of an answer that stops mid-sentence, else None."""
    stripped = (text or "").rstrip()
    if not stripped or stripped[-1] in ".!?)]}\"'`*|:…%":
        return None
    last = re.findall(r"[A-Za-z']+$", stripped)
    if last and last[0].lower() in _DANGLING:
        return last[0]
    return None
