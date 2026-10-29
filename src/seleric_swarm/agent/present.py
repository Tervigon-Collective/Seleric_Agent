"""The visuals that go with an answer: KPI cards, a table or a chart of the main result, chosen from its shape.

Runs after the answer is written and costs no model call (rows in, parts out), so it adds no latency. Nothing here
reads the question or the answer's wording to decide; the rule is the shape of the fetched result:

* a snapshot (no entity, at most two periods) with two or more measures -> KPI cards (with the change when there are
  two periods); one measure is a sentence, never a card;
* a trend (three or more periods) -> a chart; a table too when it carries two or more measures;
* a ranking (entities) -> a chart for one measure; a table for several measures or a long list;
* a multi-view result (several parts) -> ONE table, the parts joined on the entity columns they share; a cell a part
  does not have stays empty (unknown, never zero). On a shared time axis every day from every view stays (a day with
  only returns, or only sales, is still a row); on an entity ranking the leading (shortest / ranked) view still
  defines which rows exist, and the others' extras are noted as omitted.

``show_table`` (the model's own call) uses the same table builder, so both agree on labels, units and joins.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from seleric_swarm.agent.auto_chart import auto_chart, has_transcript_chart
from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.grounding import answer_numbers, is_query_payload, measured_in
from seleric_swarm.agent.mcp_toolset import TurnState
from seleric_swarm.services.metrics import is_percent_share_metric

_log = logging.getLogger("seleric.agent.present")

KPI_MAX_MEASURES = 6
MAX_SENSIBLE_PCT = 500.0  # beyond this a percent change stops informing
TABLE_MIN_ROWS = 3  # fewer rows read better as a sentence
TABLE_LONG_LIST = 10
TABLE_MAX_ROWS = 60  # what an automatic table carries (show_table keeps its own larger cap)
TREND_MIN_POINTS = 3
BLANK = "—"
UNSET_ENTITY = "(not set)"
_CURRENCY_SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}
_PERCENT_UNITS = frozenset({"percent", "pct", "%", "percentage"})


# ---- reading a result --------------------------------------------------------------------------------
def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", ""))
        except ValueError:
            return None
    return None


def query_id_of(payload: dict[str, Any]) -> str:
    prov = payload.get("provenance")
    return str(payload.get("query_id") or (prov.get("query_id") if isinstance(prov, dict) else "") or "")


@dataclass
class Source:
    """One fetched result (one part of a multi-view result) with its columns told apart."""

    query_id: str
    measures: list[str]
    time_col: str | None
    dims: list[str]
    rows: list[dict[str, Any]]
    compare_rows: list[dict[str, Any]]
    currency: str
    window: tuple[str, str] | None
    compare_window: tuple[str, str] | None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def keys(self) -> list[str]:
        return ([self.time_col] if self.time_col else []) + self.dims

    def canonical(self) -> dict[str, str]:
        """canonical key -> this source's column. Two time columns of one grain are the same axis (net sales by order
        date and ad spend by report date line up day by day), whatever their names."""
        return {(f"time:{self.time_col.split('.', 1)[1]}" if c == self.time_col else c): c for c in self.keys}


def _window(tr: Any) -> tuple[str, str] | None:
    if isinstance(tr, dict) and tr.get("start") and tr.get("end"):
        return str(tr["start"])[:10], str(tr["end"])[:10]
    return None


def source_of(payload: dict[str, Any]) -> Source | None:
    prov: dict[str, Any] = payload["provenance"] if isinstance(payload.get("provenance"), dict) else {}
    columns = [c for c in payload.get("columns") or [] if isinstance(c, str)]
    measures = [m for m in prov.get("metric_ids") or [] if m in columns]
    rows = [r for r in payload.get("rows") or [] if isinstance(r, dict)]
    if not measures or not rows:
        return None
    time_cols = [c for c in columns if "." in c and c not in measures]
    base_time = {c.split(".")[0] for c in time_cols}
    dims = [c for c in columns if c not in measures and "." not in c and c not in base_time]
    return Source(
        query_id=query_id_of(payload), measures=measures, time_col=time_cols[0] if time_cols else None, dims=dims,
        rows=rows, compare_rows=[r for r in payload.get("compare_rows") or [] if isinstance(r, dict)],
        currency=str(prov.get("currency") or ""), window=_window(prov.get("time_range")),
        compare_window=_window(prov.get("compare_period")), raw=payload)


def sources_of(payload: Any) -> list[Source]:
    """The sources of a query result: its own, or one per part of a multi-view result."""
    if not isinstance(payload, dict):
        return []
    parts = [p for p in payload.get("parts") or [] if isinstance(p, dict)]
    found = [source_of(p) for p in (parts or [payload])]
    return [s for s in found if s is not None]


# ---- labels and values -------------------------------------------------------------------------------
def humanize(column: str) -> str:
    base = column.split(".")[0] if "." in column else column
    return base.replace("_", " ").strip().title() or column


def indian_grouping(whole: int) -> str:
    digits = str(abs(whole))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join([*groups, tail])
    return ("-" if whole < 0 else "") + digits


def display_value(
    value: float | None, *, unit: str | None, aggregation: str | None, currency: str = "", fraction: bool = False
) -> str:
    """A figure the way the answer rules write it: whole rupees with Indian grouping, one decimal on a percentage,
    two on a ratio, whole counts; a missing value is a dash, never a zero."""
    if value is None:
        return BLANK
    if fraction:  # a share of a whole (clicks ÷ impressions): written as a percentage
        return f"{value * 100:.1f}%"
    code = (unit or "").strip()
    symbol = _CURRENCY_SYMBOLS.get(code.upper()) if code.upper() in _CURRENCY_SYMBOLS else (
        _CURRENCY_SYMBOLS.get(currency.upper()) if code.upper() == currency.upper() and code else None)
    if symbol is not None:
        sign = "-" if value < 0 else ""
        body = indian_grouping(round(abs(value))) if abs(value) >= 100 else f"{abs(value):.2f}"
        return f"{sign}{symbol}{body}"
    if code.lower() in _PERCENT_UNITS:
        return f"{value:.1f}%"
    if aggregation == "ratio" or (not float(value).is_integer() and abs(value) < 1000):
        return f"{value:,.2f}"
    return indian_grouping(round(value))


@dataclass
class Meta:
    """What the catalogue says about the measures of a turn."""

    labels: dict[str, str] = field(default_factory=dict)
    units: dict[str, str | None] = field(default_factory=dict)
    aggregations: dict[str, str | None] = field(default_factory=dict)
    fractions: frozenset[str] = frozenset()

    @classmethod
    def of(cls, deps: SelericDeps, measures: list[str]) -> Meta:
        cat = deps.catalogue
        label = {m.id: m.label for m in cat.metrics if m.label}
        by_id = {m.id: m for m in cat.metrics}

        def is_share(m: str) -> bool:
            """A ratio of two counts (clicks ÷ impressions, orders ÷ sessions) is a share; a ratio of two amounts
            (net sales ÷ ad spend) is a multiple. The catalogue's own components say which."""
            meta = by_id.get(m)
            parts = meta.ratio_components if meta is not None else None
            if cat.unit_for(m) != "ratio":
                return False
            if not parts:  # no components in the catalogue (hook_rate, hold_rate_15s): the share classifier decides, so 0.21 reads 21.0%
                return is_percent_share_metric(m, "ratio")
            if float(parts.get("multiplier") or 1.0) != 1.0:
                return False
            return cat.unit_for(str(parts.get("numerator"))) == "count" == cat.unit_for(str(parts.get("denominator")))

        return cls(
            labels={m: label.get(m) or humanize(m) for m in measures},
            units={m: cat.unit_for(m) for m in measures},
            aggregations={m: cat.aggregation_for(m) for m in measures},
            fractions=frozenset(m for m in measures if is_share(m)))

    def show(self, measure: str, value: float | None, currency: str = "") -> str:
        return display_value(value, unit=self.units.get(measure), aggregation=self.aggregations.get(measure),
                             currency=currency, fraction=measure in self.fractions)


def _day(value: Any) -> str:
    return str(value)[:10]


def _when(value: Any, column: str) -> str:
    """A time cell: the date, plus the hour for an hour-grain column (a day-only cell made every row of a day identical)."""
    text = str(value)
    return f"{text[:10]} {text[11:16]}" if column.endswith(".hour") and len(text) >= 16 else text[:10]


def _short_day(value: str) -> str:
    try:
        d = date.fromisoformat(value[:10])
    except ValueError:
        return value
    return f"{d.day} {d:%b}"


# ---- joining parts into one frame ---------------------------------------------------------------------
@dataclass
class Frame:
    """The rows of the main result as one table: key columns, measures, and a source per measure."""

    keys: list[str]
    time_col: str | None
    measures: list[str]
    rows: list[dict[str, Any]]
    sources: list[Source]
    joined: bool = False
    previous: Source | None = None  # the same snapshot for the period before (a second one-row result)
    sort: tuple[str, str] | None = None  # the measure the query ranked by and its direction
    snapshot: bool = False  # several one-row views of the same period merged into one row (headline numbers)
    previous_row: dict[str, Any] | None = None  # that snapshot for the period before, merged the same way
    omitted: int = 0  # rows of the other views that are not in the leading (ranked) view, left out of the join
    owner: dict[str, Source] = field(default_factory=dict)  # measure -> the view it came from (own window and basis)

    @property
    def currency(self) -> str:
        return next((s.currency for s in self.sources if s.currency), "")


def named_entities(args: dict[str, Any]) -> tuple[str, list[str]] | None:
    """The entity list the query itself named: an ``equals`` filter with several values on one dimension (the 24
    campaigns a user pasted). Every part is filtered to that list, so the rows to show are all of them."""
    for key in ("filters", "additional_filters"):
        for f in args.get(key) or []:
            if isinstance(f, dict) and f.get("operator") == "equals" and f.get("dimension"):
                values = [str(v) for v in f.get("values") or []]
                if len(values) >= 2:
                    return str(f["dimension"]), values
    return None


def frame_for(sources: list[Source], args: dict[str, Any]) -> Frame | None:
    """The frame of a result as the call that fetched it asked for it: ranked by its sort, rows named by its filters."""
    return frame_of(sources, sort_measure(args), named_entities(args))


def frame_of(sources: list[Source], lead: str | None = None, named: tuple[str, list[str]] | None = None) -> Frame | None:
    """One frame from the sources: a single one as it is; several joined on the key columns they all have (the
    source with the fewest rows leads, so a top-N defines the order), or merged into one row when none has keys.

    When the query named its entities (``named``) every part was filtered to that list, so no row is "outside": the
    join keeps all of them (a cell a view lacks stays empty: a campaign with no orders is a row, not a gap) and the
    rows come in the order the user named them.

    When the shared keys include a time axis, every day from every view stays too (sales by order date and returns by
    refund date line up day by day; a day only one view has is still a row, with the other measure empty). Entity
    rankings without a named list still drop the other views' extras: a top-N asked for those rows, not the rest."""
    if not sources:
        return None
    if len(sources) == 1:
        s = sources[0]
        return Frame(keys=s.keys, time_col=s.time_col, measures=s.measures, rows=s.rows, sources=sources)
    # the view the query ranked by leads (it defines which rows exist and their order); else the shortest list
    sources = sorted(sources, key=lambda x: (lead not in x.measures, len(x.rows)))  # stable: ties keep the order given
    measures = list(dict.fromkeys(m for s in sources for m in s.measures))
    if all(not s.keys and len(s.rows) == 1 for s in sources):
        row: dict[str, Any] = {}
        before: dict[str, Any] = {}
        for s in sources:
            row.update({m: s.rows[0].get(m) for m in s.measures})
            if len(s.compare_rows) == 1:  # each view brings its own previous-period row
                before.update({m: s.compare_rows[0].get(m) for m in s.measures})
        return Frame(keys=[], time_col=None, measures=measures, rows=[row], sources=sources, snapshot=True,
                     previous_row=before or None, owner={m: s for s in sources for m in s.measures})
    maps = [s.canonical() for s in sources]
    shared = [k for k in maps[0] if all(k in m for m in maps)]
    if not shared:  # nothing to join on: the part with the most rows stands alone
        s = max(sources, key=lambda x: len(x.rows))
        return Frame(keys=s.keys, time_col=s.time_col, measures=s.measures, rows=s.rows, sources=[s])
    first = maps[0]
    extra = list(dict.fromkeys(c for m in maps for k, c in m.items() if k not in shared))
    keys = [first[k] for k in shared] + extra
    time_key = next((k for k in shared if k.startswith("time:")), None)
    keep_all = bool(time_key) or bool(named and named[0] in shared)
    merged: dict[tuple[str, ...], dict[str, Any]] = {}
    outside: set[tuple[str, ...]] = set()
    for index, (s, m) in enumerate(zip(sources, maps, strict=True)):
        for row in s.rows:
            def cell(k: str, row: dict[str, Any] = row, m: dict[str, str] = m) -> Any:
                value = row.get(m[k])
                return str(value)[:10] if k == time_key and value is not None else value

            ident = tuple(str(cell(k) if cell(k) is not None else "") for k in shared)
            if index and ident not in merged and not keep_all:
                outside.add(ident)  # not in the leading view: it asked for these rows, not the others
                continue
            slot = merged.setdefault(ident, {first[k]: cell(k) for k in shared})
            for c in extra:
                if c in row and slot.get(c) in (None, ""):
                    slot[c] = row.get(c)
            for measure in s.measures:
                slot[measure] = row.get(measure)
    rows = list(merged.values())
    if named and named[0] in shared:  # the user's order; a row the filter did not name (none expected) goes last
        order = {v: i for i, v in enumerate(named[1])}
        rows.sort(key=lambda r: order.get(str(r.get(first[named[0]]) or ""), len(order)))
    return Frame(keys=keys, time_col=first[time_key] if time_key else None,
                 measures=measures, rows=rows, sources=sources, joined=True, omitted=len(outside))


# ---- table ---------------------------------------------------------------------------------------------
def table_content(deps: SelericDeps, frame: Frame, *, query_id: str, caption: str = "", limit: int = TABLE_MAX_ROWS) -> dict[str, Any]:
    meta = Meta.of(deps, frame.measures)
    rows = list(frame.rows)
    time_col = frame.time_col
    if time_col:  # a series reads oldest to newest
        rows.sort(key=lambda r: str(r.get(time_col) or ""))
    elif frame.sort is not None:  # a ranking: by the measure the query ranked by; rows without it come last
        field_, direction = frame.sort
        have = [r for r in rows if number(r.get(field_)) is not None]
        rows = sorted(have, key=lambda r: number(r.get(field_)) or 0.0, reverse=direction != "asc") + [
            r for r in rows if number(r.get(field_)) is None]
    shown = rows[:limit]
    headers: dict[str, str] = {}

    def header(col: str, text: str) -> str:
        name, n = text, 2
        while name in headers.values():
            name, n = f"{text} ({n})", n + 1
        headers[col] = name
        return name

    key_headers = [header(c, "Date" if c == frame.time_col else humanize(c)) for c in frame.keys]
    measure_headers = [header(m, meta.labels[m]) for m in frame.measures]
    out_rows: list[dict[str, Any]] = []
    missing = 0
    for row in shown:
        cells: dict[str, Any] = {}
        for c in frame.keys:
            raw = row.get(c)
            cells[headers[c]] = _when(raw, c) if c == frame.time_col else (str(raw) if raw not in (None, "") else UNSET_ENTITY)
        for m in frame.measures:
            value = number(row.get(m))
            missing += value is None
            cells[headers[m]] = meta.show(m, value, frame.currency)
        out_rows.append(cells)
    notes = []
    if len(rows) > len(shown):
        notes.append(f"first {len(shown)} of {len(rows)} rows")
    if frame.omitted:
        notes.append(f"{frame.omitted} more row{'s' if frame.omitted != 1 else ''} in the other view{'s' if frame.omitted != 1 else ''} not in this list")
    if missing:
        notes.append(f"{BLANK} = no data for that row in that measure (unknown, not zero)")
    full_caption = "; ".join(x for x in [caption, *notes] if x)
    content: dict[str, Any] = {"caption": full_caption, "columns": [*key_headers, *measure_headers], "rows": out_rows,
                               "query_id": query_id, "state": "ok", "total_rows": len(rows)}
    if time_col:  # the order the rows are in, so the table shows which column it is sorted by
        content["sort"] = {"column": headers[time_col], "direction": "asc"}
    elif frame.sort is not None:
        content["sort"] = {"column": headers[frame.sort[0]], "direction": "asc" if frame.sort[1] == "asc" else "desc"}
    return content


# ---- KPI cards -----------------------------------------------------------------------------------------
def delta_text(meta: Meta, measure: str, value: float, before: float, currency: str) -> tuple[str, str]:
    """(the change as it should be read, its direction): points for a share, the absolute change for an average or
    ratio (a percent of a percent misleads), a percent for a total."""
    delta = value - before
    direction = "up" if delta > 0 else "down" if delta < 0 else "flat"
    if direction == "flat":
        return "– no change", direction
    arrow = "▲" if delta > 0 else "▼"
    unit = (meta.units.get(measure) or "").lower()
    if measure in meta.fractions:
        return f"{arrow} {abs(delta) * 100:.1f} pp", direction
    if unit in _PERCENT_UNITS:
        return f"{arrow} {abs(delta):.1f} pp", direction
    pct = abs(delta / before) * 100 if before else None
    # A percent only reads right off a positive base of real size: a sign flip or a near-zero base swells it
    # (-₹292 to ₹6,873 is "2457%"), so those show the absolute change.
    if meta.aggregations.get(measure) == "ratio" or before <= 0 or value < 0 or pct is None or pct > MAX_SENSIBLE_PCT:
        return f"{arrow} {meta.show(measure, abs(delta), currency)}", direction
    return f"{arrow} {pct:.1f}%", direction


def _basis(window: tuple[str, str] | None, compare_window: tuple[str, str] | None, compared: bool) -> str:
    if not window:
        return ""
    first, last = (_short_day(d) for d in window)
    text = first if first == last else f"{first} – {last}"
    if compared and compare_window:
        c_first, c_last = (_short_day(d) for d in compare_window)
        text += f" vs {c_first}" if c_first == c_last else f" vs {c_first} – {c_last}"
    return text


def kpi_content(deps: SelericDeps, frame: Frame) -> dict[str, Any] | None:
    """Cards for a snapshot: one row (or several views merged into one), with the period before from compare_rows,
    from a second one-row result, or from two dated rows. Each card is dated by the view its measure came from."""
    if frame.joined or len(frame.measures) < 2 or len(frame.measures) > KPI_MAX_MEASURES:
        return None
    if frame.keys and not (frame.keys == [frame.time_col] and len(frame.rows) == 2):
        return None  # entities (or a longer series) are a ranking or a trend, not headline numbers
    meta = Meta.of(deps, frame.measures)
    base = frame.sources[0]
    time_col = frame.time_col
    previous: dict[str, Any] | None = None
    shared_basis = ""
    if time_col and len(frame.rows) == 2:
        ordered = sorted(frame.rows, key=lambda r: str(r.get(time_col) or ""))
        previous, current = ordered[0], ordered[1]
        shared_basis = f"{_short_day(_day(current.get(time_col)))} vs {_short_day(_day(previous.get(time_col)))}"
    elif len(frame.rows) == 1:
        current = frame.rows[0]
        if frame.previous is not None and len(frame.previous.rows) == 1:
            previous = frame.previous.rows[0]
        elif frame.previous_row:
            previous = frame.previous_row
        elif len(base.compare_rows) == 1:
            previous = base.compare_rows[0]
    else:
        return None
    cards: list[dict[str, Any]] = []
    for m in frame.measures:
        value = number(current.get(m))
        owner = frame.owner.get(m) or base
        compare_window = frame.previous.window if frame.previous is not None else owner.compare_window
        card: dict[str, Any] = {
            "label": meta.labels[m], "value": value, "display": meta.show(m, value, frame.currency),
            "unit": meta.units.get(m), "state": "ok" if value is not None else "missing",
            "time_basis": shared_basis or _basis(owner.window, compare_window, previous is not None)}
        before = number(previous.get(m)) if previous is not None else None
        if value is not None and before is not None:
            text, direction = delta_text(meta, m, value, before, frame.currency)
            comparison: dict[str, Any] = {
                "value": before, "previous_display": meta.show(m, before, frame.currency), "delta": value - before,
                "delta_display": text, "direction": direction}
            if before > 0 and value >= 0 and abs(value - before) / before * 100 <= MAX_SENSIBLE_PCT:
                comparison["delta_pct"] = round((value - before) / before * 100, 1)
            card["comparison"] = comparison
        cards.append(card)
    if all(c["state"] == "missing" for c in cards):
        return None
    return {"title": "", "cards": cards, "state": "ok"}


# ---- the plan ------------------------------------------------------------------------------------------
@dataclass
class Plan:
    kpi: bool = False
    table: bool = False
    chart: bool = False


def plan_for(frame: Frame, *, kpi_possible: bool) -> Plan:
    """Which visuals the result's shape calls for (see the module docstring)."""
    n_rows, n_measures = len(frame.rows), len(frame.measures)
    if kpi_possible:
        return Plan(kpi=True)
    if frame.time_col:  # a measure over time (optionally split by entity)
        points = len({str(r.get(frame.time_col)) for r in frame.rows})
        if points < TREND_MIN_POINTS:
            return Plan()
        # two measures (net sales + returns by day) need the table; one measure is the chart alone.
        # A long hourly series truncated to TABLE_MAX_ROWS looks like "wrong data" next to week totals in the prose.
        hour_dense = frame.time_col.endswith(".hour") and len(frame.rows) > TABLE_MAX_ROWS
        return Plan(chart=True, table=n_measures >= 2 and not hour_dense)
    if frame.keys and n_rows >= TABLE_MIN_ROWS:  # a ranking
        return Plan(chart=n_measures == 1, table=n_measures >= 2 or n_rows > TABLE_LONG_LIST)
    return Plan()


def _compatible(base: Source, other: Source, taken: set[str]) -> bool:
    """A second result of the same turn that can join the first: same entity/time columns and window, other measures
    (a net sales query and an ad spend query for the same days)."""
    return (set(other.canonical()) == set(base.canonical()) and other.window == base.window
            and not taken & set(other.measures) and not other.compare_rows and not base.compare_rows)


def main_frame(answer: str, state: TurnState) -> tuple[Frame, dict[str, Any]] | None:
    """The fetched result the answer leans on most (most of its figures quoted verbatim), as one frame.

    Other results of the turn join it when they describe the same rows with other measures (two queries for the same
    days), or are the same one-row snapshot for the other period (8 Oct and 12 Oct asked as two queries)."""
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for payload in state.payloads:
        if not isinstance(payload, dict) or not (is_query_payload(payload) or payload.get("parts")):
            continue
        parts = [p for p in payload.get("parts") or [] if isinstance(p, dict)]
        score = sum(measured_in(answer, p) for p in parts) if parts else measured_in(answer, payload)
        if score > 0:
            scored.append((score, sum(len(p.get("rows") or []) for p in (parts or [payload])), payload))
    if not scored:
        return None
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    best = scored[0][2]
    sources = sources_of(best)
    if not sources:
        return None
    previous: Source | None = None
    if len(sources) == 1 and not best.get("parts"):
        base = sources[0]
        taken = set(base.measures)
        for _, _, other in scored[1:]:
            for src in sources_of(other):
                if len(other.get("parts") or []) or src.query_id == base.query_id:
                    continue
                if _compatible(base, src, taken):
                    sources.append(src)
                    taken |= set(src.measures)
                elif (not base.keys and not src.keys and len(base.rows) == len(src.rows) == 1 and previous is None
                      and set(src.measures) == set(base.measures) and src.window and base.window
                      and src.window != base.window):
                    previous = src
    args = call_args_for(state, query_id_of(best))
    frame = frame_for(sources, args)
    if frame is None:
        return None
    apply_call_hints(frame, args)
    if previous is not None and not frame.keys and frame.sources[0].window and previous.window:
        if previous.window[0] > frame.sources[0].window[0]:  # the other result is the later period: it is the current one
            later, earlier = previous, frame.sources[0]
            frame = Frame(keys=[], time_col=None, measures=later.measures, rows=later.rows, sources=[later], previous=earlier)
        else:
            frame.previous = previous
    return frame, best


def call_args_for(state: TurnState, query_id: str) -> dict[str, Any]:
    """The arguments of the call that produced this query id; a part of a multi-view result answers with its parent's."""
    owners = {query_id}
    for payload in state.payloads:
        if isinstance(payload, dict) and any(isinstance(p, dict) and query_id_of(p) == query_id for p in payload.get("parts") or []):
            owners.add(query_id_of(payload))
    found = next((e.get("args") for e in state.evidence if e.get("query_id") in owners), None)
    return found if isinstance(found, dict) else {}


def sort_measure(args: dict[str, Any]) -> str | None:
    sort = args.get("sort")
    first = sort[0] if isinstance(sort, list) and sort and isinstance(sort[0], dict) else None
    return str(first["field"]) if first and first.get("field") else None


def apply_call_hints(frame: Frame, args: dict[str, Any]) -> None:
    """Column order and ranking come from the query the model made: its ``measures`` order (what the user listed
    first leads) and its first ``sort`` (the measure the ranking is by)."""
    asked = [m for m in args.get("measures") or [] if m in frame.measures]
    frame.measures = list(dict.fromkeys([*asked, *frame.measures]))
    sort = args.get("sort")
    first = sort[0] if isinstance(sort, list) and sort and isinstance(sort[0], dict) else None
    if first and first.get("field") in frame.measures:
        frame.sort = (str(first["field"]), str(first.get("direction") or "desc").lower())


def share_chart_type(deps: SelericDeps, frame: Frame, answer: str) -> str | None:
    """A donut (few slices) or treemap (many) when the answer itself states each row as a share of the total.

    Read from the figures, not the words: the rows are shares of a whole when the answer quotes at least two of
    their percentages of the rows' sum, the measure is additive and no value is negative."""
    if len(frame.measures) != 1 or frame.keys == [] or frame.time_col or len(frame.rows) < 3:
        return None
    measure = frame.measures[0]
    if deps.catalogue.aggregation_for(measure) != "additive":
        return None
    values = [number(r.get(measure)) for r in frame.rows]
    if any(v is None or v < 0 for v in values) or not sum(v for v in values if v):
        return None
    total = sum(v for v in values if v)
    quoted = [n["value"] for n in answer_numbers(answer) if n["pct"]]
    hits = sum(1 for v in values if v and any(abs(100 * v / total - q) <= 0.06 for q in quoted))
    if hits < 2:
        return None
    return "donut" if len(values) <= 6 else "treemap"


async def attach(deps: SelericDeps, state: TurnState, answer: str) -> dict[str, Any]:
    """Add the visuals the main result calls for to the turn's parts (and chart artifacts); returns what was added."""
    added: dict[str, Any] = {}
    if not answer.strip():
        return added
    started = time.perf_counter()
    picked = main_frame(answer, state)
    if picked is None:
        return added
    frame, payload = picked
    have = {str(p.get("type")) for p in state.parts}
    kpi = kpi_content(deps, frame)
    plan = plan_for(frame, kpi_possible=kpi is not None)
    if plan.kpi and kpi is not None and "KPI_GRID" not in have:
        state.parts.append({"type": "KPI_GRID", "content": kpi})
        added["kpi"] = len(kpi["cards"])
    qids = [s.query_id for s in frame.sources if s.query_id] or [query_id_of(payload)]
    form = share_chart_type(deps, frame, answer)
    if plan.table and "TABLE" not in have:
        state.parts.append({"type": "TABLE", "content": table_content(deps, frame, query_id=qids[0])})
        added["table"] = len(frame.rows)
    ids = list(dict.fromkeys(i for q in qids for i in state.by_query.get(q) or []))
    if (plan.chart or form) and ids and not has_transcript_chart(deps):
        artifact_id = await auto_chart(deps, state, answer, (qids[0], ids, frame.sources[0].raw), form)
        if artifact_id:
            added["chart"] = artifact_id
    if not added and "TABLE" not in have and (len(frame.rows) >= 2 or len(frame.measures) > KPI_MAX_MEASURES):
        # The prompt tells the model not to restate fetched data: never leave the answer with prose only.
        state.parts.append({"type": "TABLE", "content": table_content(deps, frame, query_id=qids[0])})
        added["table"] = len(frame.rows)
    if added:
        state.steps.append({"kind": "present", "ms": round((time.perf_counter() - started) * 1000, 1), **added})
    return added
