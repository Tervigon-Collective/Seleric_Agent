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

from collections.abc import Callable, Iterable

import re
from itertools import combinations

from seleric_swarm.services.markdown import table_cell

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

# Most words a total word and its figure may be apart and still be one claim.
# "The 30-day total is a net loss of about INR 37.6k" is 7 apart.
_MAX_CLAIM_GAP_WORDS = 10

# "overall"/"aggregate" also name a blended figure — an overall CTR or margin
# lies between the rows, it is not their sum (live MS3-c645523b51: "overall
# CTR 0.0199" over daily CTR rows was flagged as a wrong total).
_BLEND_WORDS = frozenset({"overall", "aggregate"})


def _to_float(token: str) -> float | None:
    try:
        return float(token.replace(",", "").replace("+", ""))
    except ValueError:
        return None


def _inside_identifier(text: str, start: int, end: int) -> bool:
    """A number that is part of a name ("TH-383-SUSPENDER", "BN520_TM099") is a label,
    not a figure: it touches a letter, digit or a joiner that leads into one.

    Used by the total audit too: the campaign "TH-383-SUSPENDER-UGC" read as a stated
    -383 next to the word "total", and every revision was rejected for it until the
    mission failed (live 2026-10-07 MS3-b7e85ea9bd)."""
    def joined(index: int, step: int) -> bool:
        if not 0 <= index < len(text):
            return False
        ch = text[index]
        if ch.isalnum():
            return True
        if ch in "-_/":
            nxt = index + step
            return 0 <= nxt < len(text) and text[nxt].isalnum()
        return False

    return joined(start - 1, -1) or joined(end, 1)


def ascii_minus(text: str) -> str:
    """The typographic minus (U+2212) as "-", same length so spans are unchanged.

    Models write losses and deductions as "−18,586.67". Read as unsigned, a
    correct reconciled P&L bridge (live 2026-10-08, net profit change −18,586.67)
    was rejected as summing to +82,950 and revised into a non-answer."""
    return text.replace("\u2212", "-")


def _figures(text: str) -> list[re.Match[str]]:
    """Number tokens in ``text`` that are figures (identifier digits excluded)."""
    text = ascii_minus(text)
    return [m for m in _NUMBER.finditer(text) if not _inside_identifier(text, m.start(), m.end())]


def _numbers_in(text: str) -> list[float]:
    stripped = _ISO_DATE.sub(" ", text)
    out = []
    for match in _figures(stripped):
        value = _to_float(match.group(0))
        if value is not None:
            out.append(value)
    return out


def table_cells(line: str) -> list[str]:
    """A Markdown table row's cells, split on unescaped pipes ("\\|" stays in its cell)."""
    marker = "\x00"
    return [c.replace(marker, "|").strip() for c in line.replace("\\|", marker).strip().strip("|").split("|")]


def escape_labels_in_tables(text: str, labels: Iterable[str]) -> str:
    """``text`` with every known label that holds "|" escaped inside table rows, so it stays one cell (live
    2026-10-08 MS3-ed23dd03e2: "[Google Build] PMax - Seasonal New | 27th May" written raw shifted every later
    column of its row). The labels are the dimension values the mission actually fetched."""
    piped = sorted({str(label) for label in labels if "|" in str(label)}, key=len, reverse=True)
    if not piped:
        return text
    out = []
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("|"):
            for label in piped:
                line = line.replace(label, table_cell(label))
        out.append(line)
    return "".join(out)


def ragged_table(text: str) -> str | None:
    """A table row whose cell count differs from its header's: its values sit under the wrong columns."""
    header: int | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            header = None
            continue
        cells = table_cells(stripped)
        if header is None:
            header = len(cells)
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue
        if len(cells) != header:
            return (
                f"a table row has {len(cells)} cells under a {header}-column header, so its values sit under the "
                f"wrong columns: {stripped[:120]} — a value holding '|' must be written as \\| inside its cell"
            )
    return None


def strip_markdown_tables(text: str) -> str:
    """Remove GFM Markdown tables from prose, leaving surrounding sentences.

    Live 2026-10-08: when a chart_spec is attached the UI already shows the same
    rows under the Chart widget's Table tab, so a Markdown table in
    ``final_response`` rendered the data twice. Stripping is deterministic —
    the model is still free to keep a lead sentence and interpretation.
    """
    if not text:
        return text
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if "|" in line and _TABLE_DELIM.match(nxt) and "|" in nxt:
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                i += 1
            # Drop a single blank line that only existed to separate the table.
            if out and out[-1].strip() == "" and i < len(lines) and lines[i].strip() == "":
                i += 1
            continue
        out.append(line)
        i += 1
    # Collapse runs of blank lines left where tables were removed.
    collapsed: list[str] = []
    blank = False
    for line in out:
        if not line.strip():
            if blank:
                continue
            blank = True
            collapsed.append("")
            continue
        blank = False
        collapsed.append(line)
    return "\n".join(collapsed).strip()


def unescape_literal_newlines(text: str) -> str:
    """Turn model-escaped ``\\n`` sequences into real newlines when they break tables.

    Live 2026-10-09 (thread_c8b3c93d): ``final_response`` stored the two characters
    ``\\`` + ``n`` between Markdown table rows (``| CTR |\\n| :--- |\\n| …``). The
    UI's ``SafeContent`` only splits on real newlines, so the whole table rendered
    as one paragraph with visible ``\\n``. Prefetch tables use real joins; this
    only fires when a pipe-row is glued with a literal escape.
    """
    if not text or "\\n" not in text:
        return text
    # Only rewrite when a table would otherwise stay on one physical line.
    if "\\n|" not in text and "|\\n" not in text and "\\n\\n|" not in text:
        return text
    return (
        text.replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace("\\r", "\n")
    )


def realign_markdown_tables(text: str) -> str:
    """Put an unescaped ``|`` inside a label back into that cell.

    Google campaign names are stored as ``[Google Build] Brand Search | 5th March``.
    Written into a Markdown table, the pipe adds a column, so the date is read as
    ad spend and the last metric falls off the row. Rows wider than their header
    are folded back into the first cell and the pipe is escaped.
    """
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if "|" in lines[i] and i + 1 < len(lines) and _TABLE_DELIM.match(lines[i + 1]) and "|" in lines[i + 1]:
            width = len(table_cells(lines[i]))
            out.append(lines[i])
            out.append(lines[i + 1])
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                cells = table_cells(lines[i])
                if width and len(cells) > width:
                    extra = len(cells) - width
                    cells = [" | ".join(cells[: extra + 1]), *cells[extra + 1 :]]
                if any("|" in cell for cell in cells):
                    out.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
                else:
                    out.append(lines[i])
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _table_columns(text: str, label_columns: frozenset[str] = frozenset()) -> list[list[float]]:
    return [values for values, _ in _table_columns_with_rounding(text, label_columns)]


def header_key(cell: str) -> str:
    """A table header as a catalogue-comparable key: "Campaign ID" -> "campaign_id"."""
    return "_".join(cell.strip().strip("*`").lower().split())


def _table_columns_with_rounding(
    text: str, label_columns: frozenset[str] = frozenset()
) -> list[tuple[list[float], float]]:
    """Numeric columns of every Markdown table in ``text``.

    Requires the GFM delimiter row, exactly as the UI renderer does — a block
    without one is not a table to the reader either.

    A column headed by a catalogue dimension (``label_columns``, keys as
    ``header_key`` builds them) holds labels — ids, years, pincodes — not a
    measure, so it is never summed (live 2026-10-07 MS3-4f7be7ba30: a
    campaign_id column was added up to 481,002,724,792,603,968).
    """
    lines = text.splitlines()
    columns: list[tuple[list[float], float]] = []
    i = 0
    while i < len(lines) - 1:
        if "|" in lines[i] and _TABLE_DELIM.match(lines[i + 1]) and "|" in lines[i + 1]:
            header = [header_key(c) for c in table_cells(lines[i])]
            rows: list[list[str]] = []
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                cells = table_cells(lines[i])
                # A row wider than its header has an unescaped "|" inside a value
                # (live 2026-10-08: "[Google Build] Brand Search | 5th March"); its
                # cells no longer line up with their columns, so it is not summed.
                if len(cells) == len(header):
                    rows.append(cells)
                i += 1
            width = max((len(r) for r in rows), default=0)
            for col in range(width):
                if col < len(header) and header[col] in label_columns:
                    continue
                values = []
                rounding = 0.0
                for row in rows:
                    if col >= len(row):
                        continue
                    cell = _ISO_DATE.sub(" ", row[col])
                    found = [m.group(0) for m in _figures(cell)]
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
    sentence = ascii_minus(sentence)
    for match in _SUFFIXED.finditer(sentence):
        value = _to_float(match.group(1))
        if value is None:
            continue
        consumed.append(match.span())
        scale = _SUFFIX_SCALE[match.group(2).lower()]
        out.append((value * scale, _precision_tolerance(match.group(1), scale), *match.span()))
    stripped = _ISO_DATE.sub(lambda m: " " * len(m.group(0)), sentence)
    for match in _figures(stripped):
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
                gap = sentence[end : word.start()]
                key = (word.start() - end, 1)
            elif start >= word.end():
                gap = sentence[word.end() : start]
                key = (start - word.end(), 0)
            else:
                continue
            # A figure many words away belongs to another clause: "…the highest
            # ROAS (2.75) in the ranking but did not appear in the top-5 by spend,
            # so its impact on total spend is small" bound 2.75 as the total
            # (live 2026-10-07 MS3-4f7be7ba30).
            if len(gap.split()) > _MAX_CLAIM_GAP_WORDS:
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


def _row_difference(claim: float, tolerance: float, values: list[float]) -> bool:
    """``claim`` is the change between two rows of one column (either order)."""
    if len(values) > _MAX_WINDOW_ROWS:
        return False
    slack = max(tolerance, 0.01)
    return any(abs(abs(claim) - abs(a - b)) <= slack for i, a in enumerate(values) for b in values[i + 1 :])


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


def total_mismatch(
    text: str,
    label_columns: frozenset[str] = frozenset(),
    fetched: Callable[[float, float], bool] | None = None,
) -> str | None:
    """A stated total that no column of the answer's own table can produce.

    Returns a reason string for the revision loop, or None when the answer is
    internally consistent (which includes: no table, or no total claimed).
    ``label_columns``: header keys of label (dimension) columns, never summed.
    ``fetched(value, tolerance)``: True when the mission fetched or derived that
    value. A total the data itself holds is not the table's arithmetic: "total
    product revenue 484,826.60" beside a table of the attributed part of it
    (golden Q17), or a total of a measure the table does not show.
    """
    for _sentence, claim, sums in _mismatched_totals(text, label_columns, fetched):
        return (
            f"the answer states a total of {claim:,.2f} but the table it prints "
            f"sums to {sums} — recompute the total from the rows shown, or drop it"
        )
    return None


def without_mismatched_totals(
    text: str,
    label_columns: frozenset[str] = frozenset(),
    fetched: Callable[[float, float], bool] | None = None,
) -> str | None:
    """``text`` with every sentence that states an irreconcilable total removed, or
    None when there is none. The table rows stay: they are the fetched data; the
    sentence is the model's own arithmetic on them. What an exhausted loop ships in
    place of "I could not back this answer" (live 2026-10-08 MS3-28737df764: today's
    top campaigns, every row backed, failed over one mis-added total)."""
    bad = list(dict.fromkeys(sentence for sentence, _c, _s in _mismatched_totals(text, label_columns, fetched)))
    if not bad:
        return None
    lines = []
    for line in text.splitlines():
        if "|" not in line:
            for sentence in bad:
                line = line.replace(sentence, "")
            if not line.strip() and lines and not lines[-1].strip():
                continue
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def _labelled_rows(text: str) -> list[tuple[str, list[float | None]]]:
    """(first-cell label, the numbers of the other cells) for each body row of the answer's tables."""
    rows: list[tuple[str, list[float | None]]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells or all(set(c) <= set("-: ") for c in cells):
            continue
        numbers: list[float | None] = []
        for cell in cells[1:]:
            found = _claim_candidates(cell)
            numbers.append(found[0][0] if found else None)
        rows.append((cells[0].strip("*_` ").lower(), numbers))
    return rows


def _sum_of_named_rows(sentence: str, claim: float, tolerance: float, rows: list[tuple[str, list[float | None]]]) -> bool:
    """The total of the rows the sentence itself names ("unattributed and other sources total 12,706.78"): a
    subtotal, not the table's total (regression 2026-10-10 Q17 was sent back for one)."""
    said = sentence.lower()
    named = [nums for label, nums in rows if len(label) >= 3 and label in said]
    if not named:
        return False
    width = max(len(n) for n in named)
    for col in range(width):
        vals = [n[col] for n in named if col < len(n) and n[col] is not None]
        if vals and abs(abs(claim) - abs(sum(vals))) <= max(tolerance, 0.01) * max(1, len(vals)):
            return True
    return False


def _mismatched_totals(
    text: str,
    label_columns: frozenset[str],
    fetched: Callable[[float, float], bool] | None,
):
    """(sentence, claimed total, column sums) for each stated total no column of the
    answer's table produces."""
    rounded = _table_columns_with_rounding(text, label_columns)
    columns = [values for values, _ in rounded]
    if not columns:
        return
    labelled = _labelled_rows(text)
    for line in text.splitlines():
        if "|" in line:
            continue
        for sentence in _SENTENCE_END.split(line):
            for word, claim, tolerance in _claims_on(sentence):
                if claim == 0:
                    continue
                if fetched is not None and fetched(claim, tolerance):
                    continue
                if any(_reconciles(claim, tolerance, values, cell) for values, cell in rounded):
                    continue
                # The total change between two printed rows ("the total change in net
                # profit, −18,586.67" over an event-day row and a previous-day row) is
                # the table's own arithmetic (live 2026-10-08, Q15 bridge).
                if any(_row_difference(claim, max(tolerance, cell), values) for values, cell in rounded):
                    continue
                if word in _BLEND_WORDS and any(_blend_of_rows(claim, tolerance, c) for c in columns):
                    continue
                if _sum_of_named_rows(sentence, claim, tolerance, labelled):
                    continue
                yield sentence, claim, ", ".join(f"{sum(c):,.2f}" for c in columns)
                break


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


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _sentence_start(text: str, index: int) -> bool:
    before = text[:index].rstrip(" *_`(\"'")
    return not before or before[-1] in ".!?:\n|"


def replace_metric_ids(text: str, labels: dict[str, str]) -> str:
    """Write each internal metric id in ``text`` as its catalogue display name.

    A leaked id is a wording slip with a known fix, so it is corrected in place
    instead of costing a revision: live 2026-10-07 (MS3-4f7be7ba30) the last
    revision of a finished answer was spent on "total_sales" in the prose, and
    the model chain broke during it. Whole-word matches only; ids without an
    underscore are left alone for the same reason ``leaked_metric_ids`` skips them.
    """
    for metric_id, label in labels.items():
        token = metric_id.strip()
        name = (label or "").strip()
        if len(token) < 5 or "_" not in token or not name or name == token:
            continue
        out: list[str] = []
        pos = 0
        while (hit := text.find(token, pos)) >= 0:
            end = hit + len(token)
            whole = (hit == 0 or not _is_word_char(text[hit - 1])) and (
                end == len(text) or not _is_word_char(text[end])
            )
            out.append(text[pos:hit])
            if whole:
                # "Total sales" mid-sentence reads "total sales"; an acronym ("ROAS") stays.
                lower_ok = len(name) > 1 and name[1].islower() and not _sentence_start(text, hit)
                out.append(name[0].lower() + name[1:] if lower_ok else name)
            else:
                out.append(token)
            pos = end
        out.append(text[pos:])
        text = "".join(out)
    return text


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


# An answer that hands the remaining work back to the reader instead of doing
# it. INSTRUCTIONS forbids this in plain words ("Never end your turn to ask the
# user whether you should run the next tool call or continue a lookup you have
# already started") and the model did it anyway, so it is checked like the
# arithmetic above rather than trusted.
#
# ``should I``/``shall I`` are gated behind a WORK VERB on purpose. Bare "should
# I" is also how a legitimate blocker reads — "Which brand should I use, Acme
# or Globex?" is the model correctly asking for something only the user has,
# which INSTRUCTIONS explicitly permits. An offer is about *doing the work*
# ("Should I fetch today's numbers too?"), not about which value to use.
_WORK_VERB = (
    r"(?:fetch|run|get|pull|check|compute|calculate|do|proceed|continue|add|show|"
    r"drill(?:\s+\w+)?\s?down|break(?:\s+\w+)?\s+down|re-?run|look|try|"
    r"investigate|diagnose|extend|include|map|compare|split|group)"
)
_OFFER_LEAD = re.compile(
    r"\b(?:"
    r"do you want|do you wish|would you like|would you prefer|shall we|"
    r"want me to|let me know if you (?:want|need|would like)|"
    rf"(?:should|shall) i (?:also )?{_WORK_VERB}"
    r")\b",
    re.IGNORECASE,
)
# How many trailing sentences of the closing line to consider. An offer is
# normally the last sentence; the preceding one because "…which is what I have.
# Do you want me to fetch today as well?" is the shape a live answer took
# (MS3-167d9f4838).
_OFFER_TRAILING_SENTENCES = 2
_OFFER_REASON_CHARS = 240


def ends_in_offer(text: str) -> str | None:
    """The trailing sentence that offers to do the work instead of doing it.

    Returns the offending sentence (for the revision reason) or None.

    Scoped to the answer's closing line: an offer is the last thing an answer
    says, and scanning the whole text would drag a preceding markdown table
    into the reason string. Requires an offer lead-in *and* a question mark, so
    prose that merely contains the phrase ("let me know if revenue disagrees")
    is not a hand-back.

    A false positive costs one revision and the reason states the distinction,
    so the model either finishes the work or reframes as a stated limitation.
    That is the intended trade: shipping a half-answer as ``completed`` is worse
    than one extra turn.
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines or not lines[-1].endswith("?"):
        return None
    sentences = [s for s in _SENTENCE_END.split(lines[-1]) if s and s.strip()]
    for sentence in sentences[-_OFFER_TRAILING_SENTENCES:]:
        if _OFFER_LEAD.search(sentence):
            found = " ".join(sentence.split())
            return found[:_OFFER_REASON_CHARS]
    return None
