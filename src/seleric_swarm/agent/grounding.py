"""Deterministic grounding of an answer's figures against the tool results of the turn (no model call).

Ported from test_agent's ``grounding`` / ``problems`` (synced 2026-10-13, ``app.py`` sha 422fc64e), with the number scanner
written as a character scan (no patterns). Every figure in the answer is classified as

* measured   - found in a tool result (within the rounding its own printed precision implies),
* derived    - one arithmetic step (a±b, a/b, % change, share) from two numbers that can meaningfully combine,
* unmatched  - neither,

and a figure that exists in the data but belongs to another row than the entity its line is about is ``misbound``.
A ``?`` standing in for a value is a placeholder. ``problems`` turns the findings into one plain sentence each.
"""

from __future__ import annotations

import bisect
from typing import Any

# One unit of the last digit shown is the tolerance: rounding or truncation, nothing looser.
_UNIT_SCALE = {
    "l": 1e5, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
    "cr": 1e7, "crore": 1e7, "crores": 1e7,
    "k": 1e3, "m": 1e6, "mn": 1e6,
}
_PERCENT = "%"
_UNIT_WORDS = {*_UNIT_SCALE, "x"}
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_ORDINALS = ("st", "nd", "rd", "th")
_LEADING_MARKS = "()[]*_`.,;:"
_HYPHENS = "-‐‑‒–—−"  # hyphen, non-breaking hyphen, dashes, minus: models write names with any of them

# A payload with at most this many numbers may combine freely.
SMALL_POOL = 40


# ---- answer numbers -------------------------------------------------------------------------
def _word_at(text: str, i: int) -> str:
    """The alphabetic word starting at ``i`` (lower-case), '' when none."""
    j = i
    while j < len(text) and text[j].isalpha():
        j += 1
    return text[i:j].lower()


def _word_before(text: str, i: int) -> str:
    """The alphabetic word ending just before ``i`` (skipping one space)."""
    j = i
    if j > 0 and text[j - 1] == " ":
        j -= 1
    k = j
    while k > 0 and text[k - 1].isalpha():
        k -= 1
    return text[k:j].lower()


def _is_month(word: str) -> bool:
    return len(word) >= 3 and word[:3] in _MONTHS and all(c.isalpha() for c in word)


def _date_or_time_part(text: str, start: int, end: int) -> bool:
    """The digits at [start, end) are one part of a date or time (2026-10-12, 12:30, 4/10, 12 Oct, Oct 12)."""
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    prev2 = text[start - 2] if start > 1 else ""
    next2 = text[end + 1] if end + 1 < len(text) else ""
    if after in "-:/" and next2.isdigit():
        return True
    if before in "-:/" and prev2.isdigit():
        return True
    # A code inside a hyphenated name (TH-383-SUSPENDER, SKU-12, 383-ADV) is an identifier, not a quantity.
    if before in _HYPHENS and prev2.isalpha():
        return True
    if after in _HYPHENS and next2.isalpha():
        return True
    tail = text[end:]
    skip = 1 if tail[:1] == " " else 0
    if tail[skip:skip + 2].lower() in _ORDINALS and not _word_at(tail, skip + 2):
        tail = tail[skip + 2:]
        skip = 1 if tail[:1] == " " else 0
        return _is_month(_word_at(tail, skip))
    return _is_month(_word_at(tail, skip)) or _is_month(_word_before(text, start))


def _unit_after(text: str, end: int) -> str:
    """'%' / a unit word that directly follows the number ('', when none)."""
    i = end
    if i < len(text) and text[i] == " ":
        i += 1
    if i < len(text) and text[i] == _PERCENT:
        return _PERCENT
    word = _word_at(text, i)
    if word in _UNIT_WORDS and (i + len(word) == len(text) or not text[i + len(word)].isalnum()):
        return word
    return ""


def answer_numbers(text: str) -> list[dict[str, Any]]:
    """Numbers stated in an answer with their rounding tolerance. Skips dates, times, years and small bare integers.

    Each: ``{text, value, pct, tol}``. ``value`` is scaled by a lakh/crore/k/m suffix, ``tol`` is one unit of the
    last digit shown (scaled the same way) and never below 0.01.
    """
    out: list[dict[str, Any]] = []
    i, n = 0, len(text)
    while i < n:
        if not text[i].isdigit():
            i += 1
            continue
        if i > 0 and (text[i - 1].isalpha() or text[i - 1] == "_"):  # Q3, SKU12, T00: an identifier, not a quantity
            while i < n and (text[i].isalnum() or text[i] == "_"):
                i += 1
            continue
        start = i
        digits: list[str] = []
        decimals = -1
        grouped = False
        while i < n:
            ch, nxt = text[i], (text[i + 1] if i + 1 < n else "")
            if ch.isdigit():
                digits.append(ch)
                if decimals >= 0:
                    decimals += 1
            elif ch == "," and nxt.isdigit() and decimals < 0:
                grouped = True
            elif ch == "." and nxt.isdigit() and decimals < 0:
                digits.append(".")
                decimals = 0
            else:
                break
            i += 1
        try:
            value = float("".join(digits))
        except ValueError:
            continue
        if _date_or_time_part(text, start, i):
            continue
        unit = _unit_after(text, i)
        if not unit and i < n and text[i].isalpha():  # glued to letters that are no unit: 500ml, 4XL, 26SEP -> part of a name
            continue
        if not unit and decimals < 0 and not grouped and (value < 32 or 1990 <= value <= 2100):
            continue
        mult = _UNIT_SCALE.get(unit, 1)
        tol = max(10 ** -max(decimals, 0) * mult, 0.01)
        k = start
        while k > 0 and text[k - 1] in "₹$ ":  # a sign sits before the currency mark: -₹24,562
            k -= 1
        negative = k > 0 and text[k - 1] in "-−" and (k < 2 or not text[k - 2].isalnum())
        shown = text[start:i]
        if unit and unit != _PERCENT:
            shown = text[start:i + len(unit) + (1 if text[i:i + 1] == " " else 0)]
        elif unit == _PERCENT:
            shown = text[start:i + (2 if text[i:i + 1] == " " else 1)]
        out.append({"text": shown.strip(), "value": (-value if negative else value) * mult,
                    "pct": unit == _PERCENT, "tol": tol})
    return out


# ---- payload numbers and rows ---------------------------------------------------------------
def _as_num(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def payload_numbers(o: Any, acc: list[float]) -> list[float]:
    if isinstance(o, dict):
        for v in o.values():
            payload_numbers(v, acc)
    elif isinstance(o, list):
        for v in o:
            payload_numbers(v, acc)
    elif isinstance(o, bool):
        pass
    elif isinstance(o, (int, float)):
        acc.append(float(o))
    elif isinstance(o, str):
        n = _as_num(o)
        if n is not None:
            acc.append(n)
    return acc


def _near(vals: list[float], lo: float, hi: float) -> bool:
    i = bisect.bisect_left(vals, lo)
    return i < len(vals) and vals[i] <= hi


def _hit(v: float, tol: float, vals: list[float]) -> bool:
    return _near(vals, v - tol, v + tol)


def _derived(v: float, tol: float, pct: bool, vals: list[float]) -> bool:
    """v is one arithmetic step from two pool numbers: a-b, a+b, a/b, or (percent) 100*a/b and |100*(a/b-1)|.
    Solved for b with bisect per a, so the pool needs no size cap."""
    for a in vals:
        if _near(vals, a - v - tol, a - v + tol) or _near(vals, v - tol - a, v + tol - a):
            return True
        if a <= 0:
            continue
        ratios = [(v - tol, v + tol)]
        if pct:
            ratios += [((v - tol) / 100, (v + tol) / 100), (1 + (v - tol) / 100, 1 + (v + tol) / 100),
                       (1 - (v + tol) / 100, 1 - (v - tol) / 100)]
        for r_lo, r_hi in ratios:  # a / b in [r_lo, r_hi]  <=>  b in [a / r_hi, a / r_lo]
            if r_hi > 0 and _near(vals, a / r_hi, a / r_lo if r_lo > 0 else float("inf")):
                return True
    return False


def _own(o: dict[str, Any], labels: list[str], nums: list[float]) -> None:
    """Strings (labels) and numbers of a row, looking through nested dicts but not into nested lists."""
    for v in o.values():
        if isinstance(v, dict):
            _own(v, labels, nums)
        elif isinstance(v, str):
            n = _as_num(v)
            nums.append(n) if n is not None else labels.append(v)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            nums.append(float(v))


def _norm(s: str) -> str:
    return " ".join(s.lower().replace("_", " ").split())


def payload_rows(o: Any, out: list[tuple[list[str], list[float]]], in_list: bool = False) -> list[tuple[list[str], list[float]]]:
    """[(labels, numbers)] for every dict that is an element of a list (a result row), compare rows included.
    A label shared by every row of its list is a constant column (currency, platform filter), not an entity."""
    if isinstance(o, dict):
        if in_list:
            labels: list[str] = []
            nums: list[float] = []
            _own(o, labels, nums)
            if labels and nums:
                out.append((labels, nums))
        for v in o.values():
            payload_rows(v, out)
    elif isinstance(o, list):
        mark = len(out)
        for v in o:
            payload_rows(v, out, True)
        rows = out[mark:]
        if len(rows) >= 2:
            const = set.intersection(*(set(map(_norm, lab)) for lab, _ in rows))
            out[mark:] = [([x for x in lab if _norm(x) not in const], nums) for lab, nums in rows]
    return out


def _day(s: str) -> str:
    """'2026-10-06T00:00:00.000' (a day axis value) -> '2026-10-06', the form an answer uses."""
    return s[:10] if len(s) > 10 and s[10] == "T" and s[4] == "-" and s[7] == "-" and s[:4].isdigit() else s


def _word_in(line: str, label: str) -> bool:
    """label occurs in line as whole words."""
    i = line.find(label)
    while i >= 0:
        j = i + len(label)
        if (i == 0 or not line[i - 1].isalnum()) and (j == len(line) or not line[j].isalnum()):
            return True
        i = line.find(label, i + 1)
    return False


def _entities(line: str, labels: Any) -> set[str]:
    """Longest-match labels named on this line."""
    found = [lab for lab in labels if _word_in(line, lab)]
    return {lab for lab in found if not any(lab != o and lab in o for o in found)}


def placeholders(answer: str) -> list[str]:
    """A '?' standing in for a value ('?', '7?', '₹16,227.73?'). A question that ends a line is not one."""
    out: list[str] = []
    for line in answer.split("\n"):
        toks = line.split()
        for i, t in enumerate(toks):
            core = t.strip(_LEADING_MARKS)
            if core == "?":
                out.append("?")
            elif core.endswith("?") and i < len(toks) - 1 and _as_num(core[:-1].lstrip("₹$").rstrip("%")) is not None:
                out.append(core)
    return out


def is_query_payload(p: Any) -> bool:
    prov = p.get("provenance") if isinstance(p, dict) else None
    return isinstance(p, dict) and bool(p.get("query_id") or (isinstance(prov, dict) and prov.get("query_id")))


def grounding(answer: str, payloads: list[Any]) -> dict[str, Any]:
    """Classify each answer number (see module docstring). Arithmetic is limited to a row's own entity plus totals
    (and tiny payloads): across hundreds of numbers any figure is a sum or ratio of some pair, so an unrestricted pair
    scan accepts almost anything."""
    vals = sorted({abs(p) for p in payload_numbers(payloads, [])})
    by_label: dict[str, set[float]] = {}
    shown: dict[str, str] = {}
    loose: set[float] = set()
    small: list[list[float]] = []
    labelled: set[float] = set()
    for p in payloads:
        if not is_query_payload(p):
            continue  # catalogue/metadata results can be quoted (measured) but never feed arithmetic or entity labels
        prov = p.get("provenance")
        metric_names = {_norm(m) for m in (p.get("columns") or []) + ((prov or {}).get("metric_ids") or []) if isinstance(m, str)}
        pn = {abs(x) for x in payload_numbers(p, [])}
        in_rows: set[float] = set()
        for labels, nums in payload_rows(p, []):
            in_rows.update(abs(n) for n in nums)
            for lab in labels:
                lab = _day(lab)
                if len(lab) >= 4 and _as_num(lab) is None and _norm(lab) not in metric_names:  # a metric name is not an entity
                    by_label.setdefault(_norm(lab), set()).update(abs(n) for n in nums)
                    shown.setdefault(_norm(lab), lab)
        loose |= pn - in_rows  # totals, deltas, percentages: numbers outside any labelled row
        labelled |= in_rows
        if len(pn) <= SMALL_POOL:
            small.append(sorted(pn))
    ans_nums = answer_numbers(answer)
    out: dict[str, Any] = {"numbers": len(ans_nums), "measured": 0, "derived": [], "unmatched": [], "misbound": [],
                           "placeholders": placeholders(answer)}

    def classify(n: dict[str, Any], pool: list[float], extra: Any = ()) -> str | None:
        v, tol = abs(n["value"]), n["tol"] * 1.01  # 1% slack so x.x5 rounding half-up still matches
        if _hit(v, tol, pool) or (n["pct"] and _hit(v / 100, tol / 100, pool)):
            return "measured"
        return "derived" if any(_derived(v, tol, n["pct"], sorted(set(pool_))) for pool_ in extra) else None

    # Which numbers are about which entity: a line naming entities binds its own numbers; an entity line with no
    # numbers (a header) binds the lines after it until a blank line or another entity.
    scoped: dict[str, set[str]] = {}
    line_operands: dict[str, list[float]] = {}  # figure -> the other non-percent figures on its line
    carry: set[str] = set()
    for line in answer.split("\n"):
        if not line.strip():
            carry = set()
            continue
        ents = _entities(_norm(line), by_label)
        line_nums = answer_numbers(line)
        for n in line_nums:
            line_operands.setdefault(
                n["text"] + "|" + str(n["value"]),
                [abs(o["value"]) for o in line_nums if o is not n and not o["pct"]],
            )
        scope = ents or carry
        if ents and not line_nums and max(map(len, ents)) * 2 >= len(_norm(line.strip("#*-|: "))):
            carry = ents  # a header that is mostly the entity name; a passing mention inside prose is not one
        for n in line_nums:
            if scope:
                scoped.setdefault(n["text"] + "|" + str(n["value"]), scope)

    def group(scope: set[str]) -> set[float]:
        return set(loose).union(*(by_label[lab] for lab in scope)) if scope else set(loose)

    # A figure may be built from values the answer itself already established (contribution = sales - cogs, then the
    # change in contribution): those intermediates were each verified, so they join the pool. Repeat until stable.
    accepted: set[float] = set()
    status: dict[int, str] = {}
    pending = list(range(len(ans_nums)))
    for _ in range(3):
        left = []
        for i in pending:
            an = ans_nums[i]
            key = an["text"] + "|" + str(an["value"])
            scope = scoped.get(key, set())
            pool = group(scope) | (labelled if len(labelled) <= SMALL_POOL and not scope else set()) | accepted
            operands = line_operands.get(key, [])
            # A percentage written beside two or more figures is a claim about THEM (34,377 -> 42,177 (+22.7%)): it must
            # follow from those, not from any pair among the hundreds of numbers in the data (a wrong +23.0% did).
            on_line = [sorted(set(operands))] if an["pct"] and len(operands) >= 2 else None
            kind = classify(an, vals, on_line if on_line is not None else [pool, *small])
            if kind:
                status[i] = kind
                accepted.add(abs(an["value"]))
            else:
                left.append(i)
        if len(left) == len(pending):
            break
        pending = left
    for i, an in enumerate(ans_nums):
        kind = status.get(i)
        if kind == "measured":
            out["measured"] += 1
        elif kind == "derived":
            out["derived"].append(an["text"])
        else:
            out["unmatched"].append(an["text"])
            continue
        scope = scoped.get(an["text"] + "|" + str(an["value"]), set())
        if scope and not classify(an, sorted(group(scope)), [group(scope) | accepted, *small]):
            owners = sorted(lab for lab, ns in by_label.items() if lab not in scope and classify(an, sorted(ns)) == "measured")
            if len(owners) == 1:  # a figure found in several rows is a coincidence, not a swap
                out["misbound"].append(
                    f"{an['text']} on the line about {', '.join(sorted(shown[s] for s in scope))} is not in that row; "
                    f"it is in the row for {', '.join(shown[o] for o in owners[:3])}")
    return out


def measured_in(answer: str, payload: Any) -> int:
    """How many figures of the answer appear verbatim in this one payload (the same tolerance ``grounding`` uses)."""
    vals = sorted({abs(p) for p in payload_numbers(payload, [])})
    hits = 0
    for n in answer_numbers(answer):
        v, tol = abs(n["value"]), n["tol"] * 1.01
        if _hit(v, tol, vals) or (n["pct"] and _hit(v / 100, tol / 100, vals)):
            hits += 1
    return hits


def problems(g: dict[str, Any]) -> list[str]:
    """Findings from ``grounding`` as one plain sentence each."""
    pcts = [u for u in g["unmatched"] if u.rstrip().endswith("%")]
    other = [u for u in g["unmatched"] if u not in pcts]
    return ([f"figures in no tool result and not one arithmetic step from them: {', '.join(other)}"] if other else []) + \
        ([f"percentages that do not follow from the figures written beside them (recompute from those figures): {', '.join(pcts)}"] if pcts else []) + \
        [f"wrong row: {m}" for m in g["misbound"]] + \
        ([f"placeholder instead of a value: {', '.join(g['placeholders'])}"] if g["placeholders"] else [])


CORRECTION = (
    "Verification found problems in the draft:\n- {issues}\n"
    "Fix only those figures. Replace each with the exact value from the tool results (rounding for display is fine), "
    "or, if the data does not hold it, say it is unavailable - a '?' or a guessed value is never acceptable. If a flagged "
    "figure was a sum you computed, fetch the total with a tool (granularity none) instead of adding rows. "
    "Keep everything else the user asked for: every table, row, breakdown and period stays in the answer. "
    "Write the complete final answer without mentioning this check and without repeating any figure you are removing."
)


def correction_message(issues: list[str]) -> str:
    return CORRECTION.format(issues="\n- ".join(issues))


def unverified_note(issues: list[str]) -> str:
    return "\n\n⚠ Not verified against the data: " + "; ".join(issues)


def grounded_count(g: dict[str, Any]) -> int:
    """Figures of an answer that check out (measured + derived)."""
    return int(g.get("measured", 0)) + len(g.get("derived") or [])


def lost_substance(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """A correction that cut the answer's verified figures to under 60% of what the draft had verified (while the draft
    had at most a quarter of its figures flagged) traded the answer for a clean-looking one: keep the draft."""
    flagged = len(before.get("unmatched") or []) + len(before.get("misbound") or [])
    mostly_fine = before.get("numbers", 0) > 0 and flagged * 4 <= before["numbers"]
    return mostly_fine and grounded_count(after) < 0.6 * grounded_count(before)
