"""A ratio shown beside its own parts must equal them, row by row.

Live 2026-10-09 (MS3-dce7d3104e, "Why did ROAS rise compared with last week?"): the comparison row read
Net ROAS 0.894x next to contribution margin 155,256 and ad spend 164,198 — which make 0.946x. The diagnosis
had compared 09-29..10-02; a revision fetched net ROAS alone for 09-28..10-02 and patched only the headline,
so one row mixed two periods and every gate passed it (each figure was individually backed).

Nothing here names a metric or a formula. A relationship ``ratio = k × part ÷ other part`` is learned from the
mission's own evidence (the same period and slice holding all three, on at least three different slices or days
with a constant k), and table columns are matched to metrics by their catalogue label. Only then is a row
checked, against the precision the row's own figures are shown with.
"""

from __future__ import annotations

import itertools
import math
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from seleric_swarm.agent.validation.answer_audit import _TABLE_DELIM, _figures, _to_float, table_cells

# k must be the same on every slice it was seen on (a real identity, not a coincidence).
_K_AGREEMENT = 0.005
# The slices only verify k when their part ratios differ by at least this much.
_DISTINCT = 0.01
# A free scale k fits any two slices (live: "MER = 2.75 × ad spend ÷ net sales" from two periods), so a
# relationship needs at least three slices that agree.
_MIN_SLICES = 3
# A row is incoherent when its ratio is off by more than its display rounding plus this share of the value.
_ROW_TOLERANCE = 0.01


def _words(text: str) -> tuple[str, ...]:
    """Lower-case words of a header or label, without what is in brackets (units)."""
    depth = 0
    buf: list[str] = []
    for ch in text.lower():
        if ch in "([":
            depth += 1
            continue
        if ch in ")]":
            depth = max(0, depth - 1)
            continue
        if depth:
            continue
        buf.append(ch if ch.isalnum() or ch == "%" else " ")
    return tuple("".join(buf).split())


def _identity(payload: dict[str, Any]) -> tuple[Any, ...]:
    dims = payload.get("dimensions") or {}
    return (
        str(payload.get("period_start")),
        str(payload.get("period_end")),
        tuple(sorted((str(k), str(v)) for k, v in dims.items())),
    )


def _learn_ratios(
    payloads: Iterable[dict[str, Any]], aggregation_for: Any
) -> list[tuple[str, str, str, float]]:
    """(ratio, numerator, denominator, k) relationships the evidence itself verifies."""
    by_slice: dict[tuple[Any, ...], dict[str, float]] = defaultdict(dict)
    for p in payloads:
        mid, value = p.get("metric_id"), p.get("value")
        if not mid or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            continue
        by_slice[_identity(p)][str(mid)] = float(value)
    metrics = {m for vals in by_slice.values() for m in vals}
    ratios = [m for m in metrics if aggregation_for(m) == "ratio"]
    parts = [m for m in metrics if aggregation_for(m) == "additive"]
    found: list[tuple[str, str, str, float]] = []
    for r in ratios:
        for n, d in itertools.permutations(parts, 2):
            ks: list[float] = []
            quotients: list[float] = []
            for vals in by_slice.values():
                if r in vals and n in vals and d in vals and vals[d] and vals[n] and vals[r]:
                    q = vals[n] / vals[d]
                    ks.append(vals[r] / q)
                    quotients.append(q)
            if len(ks) < _MIN_SLICES:
                continue
            k = sorted(ks)[len(ks) // 2]
            if not all(abs(x / k - 1) <= _K_AGREEMENT for x in ks):
                continue
            # a constant ratio verifies nothing: the parts' quotient must vary across the slices
            if max(quotients) - min(quotients) < _DISTINCT * max(abs(q) for q in quotients):
                continue
            found.append((r, n, d, k))
    return found


def _cell_value(cell: str) -> tuple[float, float] | None:
    """(value, half a unit of its last shown digit) of a one-figure cell; percents as fractions."""
    text = cell.strip()
    # a multiple ("1.04x", "0.95×") is a figure with its unit, not a label
    if text[-1:] in ("x", "×") and text[-2:-1].isdigit():
        text = text[:-1]
    figures = [m.group(0) for m in _figures(text)]
    if len(figures) != 1:
        return None
    token = figures[0]
    value = _to_float(token)
    if value is None:
        return None
    digits = token.replace(",", "").split(".")
    half = 0.5 * 10 ** (-len(digits[1])) if len(digits) == 2 else 0.5
    if "%" in cell:
        return value / 100.0, half / 100.0
    return value, half


def _tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    lines = text.splitlines()
    out: list[tuple[list[str], list[list[str]]]] = []
    i = 0
    while i < len(lines) - 1:
        if "|" in lines[i] and "|" in lines[i + 1] and _TABLE_DELIM.match(lines[i + 1]):
            header = table_cells(lines[i])
            rows = []
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                cells = table_cells(lines[i])
                if len(cells) == len(header):
                    rows.append(cells)
                i += 1
            out.append((header, rows))
            continue
        i += 1
    return out


def ratio_incoherence(text: str, payloads: list[dict[str, Any]], catalogue: Any) -> str | None:
    """A reason when a table row shows a ratio that its own parts in the same row do not give."""
    aggregation_for = getattr(catalogue, "aggregation_for", None)
    label_for = getattr(catalogue, "label_for", None)
    if not text or aggregation_for is None or label_for is None or "|" not in text:
        return None
    relations = _learn_ratios(payloads, aggregation_for)
    if not relations:
        return None
    names: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    for r, n, d, _ in relations:
        for m in (r, n, d):
            names[m].add(_words(m.replace("_", " ")))
            if label := label_for(m):
                names[m].add(_words(label))
    for header, rows in _tables(text):
        keys = [_words(h) for h in header]

        def column(metric: str, keys: list[tuple[str, ...]] = keys) -> int | None:
            hits = [i for i, k in enumerate(keys) if k and k in names[metric]]
            return hits[0] if len(hits) == 1 else None

        for r, n, d, k in relations:
            cols = (column(r), column(n), column(d))
            if None in cols or len(set(cols)) < 3:
                continue
            ri, ni, di = cols  # type: ignore[misc]
            for row in rows:
                rv, nv, dv = _cell_value(row[ri]), _cell_value(row[ni]), _cell_value(row[di])
                if rv is None or nv is None or dv is None or not dv[0] or not nv[0]:
                    continue
                expected = k * nv[0] / dv[0]
                # what the parts' own rounding allows, plus the ratio's rounding and a small margin
                slack = abs(expected) * (nv[1] / abs(nv[0]) + dv[1] / abs(dv[0])) + rv[1] + _ROW_TOLERANCE * abs(expected)
                if abs(rv[0] - expected) <= slack:
                    continue
                label = row[0] if row and row[0] else "a row"
                return (
                    f"in the table row '{label}', {header[ri]} is {row[ri]} but {header[ni]} {row[ni]} and "
                    f"{header[di]} {row[di]} in the same row give {expected:.4g} — the row mixes figures from "
                    "different periods or fetches. Take every figure in a row from the same period and the same "
                    "tool result (re-fetch the parts for that period if you changed it), and keep the headline "
                    "consistent with the table"
                )
    return None
