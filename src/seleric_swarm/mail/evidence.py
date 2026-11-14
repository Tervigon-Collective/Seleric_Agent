"""Excel datasets from the evidence a thread's queries stored: the original values, never display strings or numbers the
model retyped. A query with nothing stored is returned as missing so the caller can say so instead of dropping it."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.present import _PERCENT_UNITS, BLANK, Meta, display_value, humanize
from seleric_swarm.conversations.contracts import Artifact
from seleric_swarm.mail.chart import chart_png
from seleric_swarm.mail.draft import EmailChart, EmailTable
from seleric_swarm.mail.excel import Column, Dataset
from seleric_swarm.mail.send import Attachment

_CUT = re.compile(r"(\d+) of (\d+) rows")
_DAILY_OR_COARSER = {"day", "week", "month"}


def _mission_ids(deps: SelericDeps) -> list[str]:
    """This turn's mission, plus every mission of the thread that left a turn record."""
    found = [deps.mission_id]
    listing = getattr(deps.artifact_store, "list_for_context", None)
    if callable(listing):
        found += [a.mission_id for a in listing(deps.principal.workspace_id, deps.thread_id)
                  if a.artifact_type == "turn_record" and a.mission_id]
    return list(dict.fromkeys(found))


def _kind(meta: Meta, metric: str, currency: str) -> tuple[str, float]:
    """(Excel kind, divisor): a stored share is a fraction, a percent unit is already ×100, a currency is an amount."""
    unit = (meta.units.get(metric) or "").strip()
    if metric in meta.fractions:
        return "percent", 1.0
    if unit.lower() in _PERCENT_UNITS:
        return "percent", 100.0
    if unit and (unit.upper() == currency.upper() or len(unit) == 3 and unit.isupper()):
        return "currency", 1.0
    return "number", 1.0


def _window(meta: dict[str, Any] | None) -> str:
    return f"{meta['start']} to {meta['end']}" if isinstance(meta, dict) and meta.get("start") else ""


def _dataset(arts: list[Artifact], meta: Meta, definitions: dict[str, str], tz: str) -> Dataset:
    prov = arts[0].provenance.source_metadata or {}
    currency = str(prov.get("currency") or "")
    bodies = [a.payload for a in arts]
    grain = bodies[0].get("grain", "none")
    dims = list(dict.fromkeys(k for b in bodies for k in b.get("dimensions") or {}))  # the query's own column order
    metrics = list(dict.fromkeys(b["metric_id"] for b in bodies))
    has_previous = any(b.get("period") == "previous" for b in bodies)
    kinds = {m: _kind(meta, m, currency) for m in metrics}

    columns = [Column(d, humanize(d.split(".")[-1])) for d in dims]
    if grain == "hour":
        columns.append(Column("period_start", "Hour"))
    elif grain in _DAILY_OR_COARSER:
        columns.append(Column("period_start", "Period start", "date"))
    else:  # a total over a window: say which window each row covers
        columns += [Column("period_start", "Period start", "date"), Column("period_end", "Period end", "date")]
    if has_previous:
        columns.append(Column("period", "Period"))
    for m in metrics:
        label = meta.labels.get(m, humanize(m))
        suffix = f" ({currency or meta.units.get(m)})" if kinds[m][0] == "currency" else ""
        columns.append(Column(m, label + suffix, kinds[m][0], unit=currency or str(meta.units.get(m) or "")))  # type: ignore[arg-type]

    rows: dict[tuple, dict[str, Any]] = {}
    for b in bodies:
        key = (tuple(sorted((b.get("dimensions") or {}).items())), b["period_start"], b["period_end"], b.get("period"))
        row = rows.setdefault(key, {**(b.get("dimensions") or {}), "period_start": b["period_start"][:16 if grain == "hour" else 10].replace("T", " "),
                                    "period_end": b["period_end"][:10], "period": b.get("period", "current")})
        value = b.get("value")
        row[b["metric_id"]] = None if value is None else value / kinds[b["metric_id"]][1]

    cut = _CUT.search(str(prov.get("evidence_truncated") or ""))
    window = _window(prov.get("time_range"))
    if has_previous and _window(prov.get("compare_period")):
        window += f" (compared with {_window(prov.get('compare_period'))})"
    args = bodies[0].get("source_query") or {}
    filters = json.dumps(args.get("filters"), ensure_ascii=False) if args.get("filters") else ""
    shown = [meta.labels.get(m, humanize(m)) for m in metrics[:2]]
    return Dataset(
        name=" and ".join(shown) + (" by " + " and ".join(humanize(d.split(".")[-1]) for d in dims[:2]) if dims else ""),
        columns=columns, rows=list(rows.values()), period=window, timezone=tz, filters=filters,
        source=f"query {arts[0].provenance.query_version}" + (f", data as of {prov['freshness']}" if prov.get("freshness") else ""),
        definitions={meta.labels.get(m, m): definitions[m] for m in metrics if definitions.get(m)},
        total_rows=int(cut[2]) if cut else None)


def _part_ids(deps: SelericDeps, qid: str) -> list[str]:
    """A multi-view parent id from an earlier turn: its rows are stored under the parts' ids, which only the turn's payloads link."""
    for mission in _mission_ids(deps):
        for a in deps.artifact_store.list_for_mission(mission):
            if a.artifact_type == "turn_payloads" and isinstance(a.payload, dict):
                for pl in a.payload.get("payloads") or []:
                    if isinstance(pl, dict) and pl.get("query_id") == qid:
                        return [str(x["query_id"]) for x in pl.get("parts") or [] if isinstance(x, dict) and x.get("query_id")]
    return []


def _collect(deps: SelericDeps, query_ids: Iterable[str], by_query: dict[str, list[str]] | None
             ) -> tuple[dict[str, list[Dataset]], list[str]]:
    """Datasets per requested query id (a parent id can hold several parts), and the ids with nothing stored."""
    store = deps.artifact_store
    stored: dict[str, Artifact] = {}
    for mission in _mission_ids(deps):
        for a in store.list_for_mission(mission):
            if a.artifact_type == "evidence" and isinstance(a.payload, dict):
                stored[a.id] = a
    meta = Meta.of(deps, [a.payload["metric_id"] for a in stored.values()])
    definitions = {m.id: str(m.raw.get("description") or m.raw.get("definition") or "") for m in deps.catalogue.metrics}
    tz = getattr(deps.as_of.tzinfo, "key", None) or str(deps.as_of.tzinfo or "UTC")
    found: dict[str, list[Dataset]] = {}
    missing: list[str] = []
    for qid in dict.fromkeys(query_ids):
        arts = {a.id: a for a in stored.values() if a.provenance.query_version == qid}
        arts.update({i: stored[i] for i in (by_query or {}).get(qid, []) if i in stored})
        if not arts:
            parts = _part_ids(deps, qid)
            arts = {a.id: a for a in stored.values() if a.provenance.query_version in parts}
        if not arts:
            missing.append(qid)
            continue
        by_part: dict[str, list[Artifact]] = {}
        for a in arts.values():
            by_part.setdefault(a.provenance.query_version, []).append(a)
        found[qid] = [_dataset(group, meta, definitions, tz) for group in by_part.values()]
    return found, missing


def datasets_for(deps: SelericDeps, query_ids: Iterable[str], *, by_query: dict[str, list[str]] | None = None
                 ) -> tuple[list[Dataset], list[str]]:
    """(datasets, query ids with nothing stored). ``by_query`` is this turn's parent id -> evidence ids map."""
    found, missing = _collect(deps, query_ids, by_query)
    return [d for group in found.values() for d in group], missing


def _cell(value: Any, col: Column) -> str:
    if value is None:
        return BLANK
    if col.kind == "currency":
        return display_value(value, unit=col.unit, aggregation=None, currency=col.unit)
    if col.kind == "percent":
        return f"{value * 100:.1f}%"
    if col.kind == "number":
        return display_value(value, unit=None, aggregation=None)
    return str(value)


def _email_table(ds: Dataset, max_rows: int, attached: bool) -> EmailTable:
    skip = {"period_end"} | ({"period_start"} if ds.columns and any(c.key == "period_end" for c in ds.columns) else set())
    cols = [c for c in ds.columns if c.key not in skip]  # a window total's dates are the subtitle, not a column
    shown = ds.rows[:max_rows]
    notes = []
    if ds.partial:
        notes.append(f"Partial data: only {len(ds.rows)} of {ds.total_rows} rows were stored.")
    if len(shown) < len(ds.rows):
        notes.append(f"Showing {len(shown)} of {len(ds.rows)} rows." + (" The full data is in the attachment." if attached else ""))
    return EmailTable(
        title=ds.name, subtitle=ds.period, headers=["Date" if c.key == "period_start" else c.header for c in cols],
        rows=[[_cell(r.get(c.key), c) for c in cols] for r in shown],
        align=["r" if c.kind in ("currency", "percent", "number") else "l" for c in cols], note=" ".join(notes))


def tables_for(deps: SelericDeps, query_ids: Iterable[str], *, by_query: dict[str, list[str]] | None = None,
               max_rows: int = 10, attached: bool = False) -> tuple[dict[str, list[EmailTable]], list[str]]:
    """Body tables per requested query id (formatted like the answers), and the ids with nothing stored."""
    found, missing = _collect(deps, query_ids, by_query)
    return {qid: [_email_table(d, max_rows, attached) for d in group] for qid, group in found.items()}, missing


def charts_for(deps: SelericDeps, query_ids: Iterable[str], *, by_query: dict[str, list[str]] | None = None
               ) -> tuple[dict[str, list[tuple[EmailChart, Attachment]]], list[str]]:
    """Inline trend charts per requested query id (empty when the result is not a time series), and the ids with nothing stored."""
    found, missing = _collect(deps, query_ids, by_query)
    out: dict[str, list[tuple[EmailChart, Attachment]]] = {}
    for qid, group in found.items():
        out[qid] = []
        for i, ds in enumerate(group, start=1):
            if (png := chart_png(ds)) is not None:
                slug = re.sub(r"[^A-Za-z0-9]+", "-", ds.name).strip("-")[:40] or "chart"
                cid = f"chart-{re.sub(r'[^A-Za-z0-9]+', '', qid)[:24]}-{i}@seleric"
                out[qid].append((EmailChart(ds.name, cid), Attachment(f"{slug}.png", png, "image/png", cid)))
    return out, missing
