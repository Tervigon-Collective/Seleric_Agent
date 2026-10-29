"""The Core MCP read tools as PydanticAI tools for the single reasoning loop.

Schemas are read from the server (``tools/list``) once and cached, so the model sees exactly the contract the server
publishes. Each call is validated against that schema, checked for known misuse, run through ``MCPGateway`` with a
timeout, flagged when the window is still being written, deduplicated within the turn, compacted without losing a
number, and (for query results) written as ``EvidenceArtifact`` rows so charts, sources and the local analysis tools
keep working. Failures are returned to the model as tool results, never raised.

Ported from test_agent's ``compact`` / ``filter_misuse`` / ``flag_partial`` / ``flag_composed`` / ``validate``
(synced 2026-10-13, ``app.py`` sha 422fc64e).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import tzinfo as TzInfo
from typing import Any
from zoneinfo import ZoneInfo

import jsonschema
from pydantic_ai import RunContext
from pydantic_ai.tools import Tool

from seleric_swarm.agent import analysis_context
from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.services.mcp_query import relabel_undated, with_date_axis

_log = logging.getLogger("seleric.agent.mcp_toolset")

# Read-only allowlist. Server annotations carry no readOnlyHint, so we never trust them.
ALLOW: tuple[str, ...] = (
    "catalogue_search_metrics", "catalogue_get_metrics", "catalogue_get_metric", "catalogue_list_dimensions",
    "catalogue_resolve_dimension", "catalogue_resolve_term", "catalogue_resolve_concept", "catalogue_resolve_values",
    "catalogue_resolve_brand", "catalogue_list_brands", "catalogue_get_ontology", "catalogue_related_metrics",
    "modules_list", "metrics_query", "metrics_drilldown", "insights_explain", "build_dataset_query",
)
QUERY_TOOLS = frozenset({"metrics_query", "metrics_drilldown"})
DATASET_TOOL = "build_dataset_query"
DATASET_PAGE = 5000  # rows per get page (the server's maximum)
MAX_DATASET_EVIDENCE = int(os.getenv("AGENT_MAX_DATASET_EVIDENCE", "5000"))  # evidence points per dataset (an artifact write each; raise only after timing the store)

MAX_ROWS = int(os.getenv("AGENT_MAX_ROWS", "60"))  # list cap inside a tool result
MAX_RESULT_CHARS = int(os.getenv("AGENT_MAX_RESULT_CHARS", "14000"))
TOOL_TIMEOUT_S = float(os.getenv("AGENT_TOOL_TIMEOUT_S", "45"))
MAX_EVIDENCE_ROWS = 5000  # evidence points (row × measure) written per query result; going over is flagged, never silent

IST = ZoneInfo("Asia/Kolkata")

DROP_KEYS = {"cube_query", "semantic", "catalogue_version", "generated_at", "cube_sql", "clickhouse_trace",
             "lookup_sql", "catalogue_sha", "serve_objects", "openmetadata"}
# An empty result set is information ("no rows"), unlike an empty plumbing field: it stays in what the model sees.
KEEP_WHEN_EMPTY = frozenset({"rows", "compare_rows"})
VALUELESS_OPS = ("set", "notSet")  # "is not null" / "is null": the server ignores any values and returns the unfiltered result


# ---- results: compact without losing numbers -------------------------------------------------
def _shrink(o: Any, cap: int) -> Any:
    if isinstance(o, dict):  # explicit nulls stay: a NULL cell (e.g. no LPV for Google) is not the same as "not fetched"
        return {k: _shrink(v, cap) for k, v in o.items()
                if k not in DROP_KEYS and (v not in ([], {}, "") or (k in KEEP_WHEN_EMPTY and v == []))}
    if isinstance(o, list):
        out = [_shrink(v, cap) for v in o[:cap]]
        return out + [f"... {len(o) - cap} more items truncated (narrow the query or sort+limit)"] if len(o) > cap else out
    return o


def compact_value(payload: Any, *, max_rows: int = MAX_ROWS, max_chars: int = MAX_RESULT_CHARS) -> Any:
    """``payload`` without plumbing and with long lists capped (explicit marker), as a value that is still valid data:
    the cap halves until the result fits ``max_chars`` or the lists are down to 5 items."""
    cap = max_rows
    while True:
        shrunk = _shrink(payload, cap)
        size = len(json.dumps(shrunk, separators=(",", ":"), ensure_ascii=False, default=str))
        if size <= max_chars or cap <= 5:
            return shrunk
        cap //= 2


def compact(payload: Any, *, max_rows: int = MAX_ROWS, max_chars: int = MAX_RESULT_CHARS) -> str:
    """Drop plumbing, cap long lists (explicit marker), never cut through a number."""
    s = json.dumps(compact_value(payload, max_rows=max_rows, max_chars=max_chars), separators=(",", ":"),
                   ensure_ascii=False, default=str)
    return s if len(s) <= max_chars else s[:max_chars] + f'..."[TRUNCATED {len(s) - max_chars} chars]"'


def _clean_schema(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: _clean_schema(v) for k, v in o.items() if k != "title"}
    return [_clean_schema(v) for v in o] if isinstance(o, list) else o


def _desc(description: str) -> str:
    return " ".join((description or "").split())


# ---- misuse and completeness checks ----------------------------------------------------------
def filter_misuse(args: dict[str, Any]) -> str | None:
    """A set/notSet filter that carries values would not filter to them (it silently returns the unfiltered total).
    Membership is 'equals' with a values list."""
    for key in ("filters", "additional_filters"):
        for f in args.get(key) or []:
            if isinstance(f, dict) and f.get("operator") in VALUELESS_OPS and f.get("values"):
                return (f"filter on '{f.get('dimension')}': operator '{f['operator']}' tests for null / not null and ignores "
                        f"values, so it does NOT restrict to {f['values']}. To keep only certain values use operator 'equals' "
                        f"with the values list.")
    return None


def ist_today() -> date:
    return datetime.now(IST).date()


def _iso_day(value: Any) -> date | None:
    text = str(value or "")
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    return None


def _iso_moment(value: Any, tz: TzInfo) -> datetime | None:
    """Cube time bucket ('2026-10-10T14:00:00.000') as an aware datetime; date-only becomes midnight.

    A bucket carries no offset because Cube reports it in the query's own timezone, so it is stamped with ``tz`` (the
    mission calendar), never converted from UTC: that shifted every hour by 5:30 and pushed the evening hours of a day
    onto the next one in any per-day roll-up."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text[:19] if len(text) >= 19 and text[10] == "T" and "+" not in text[10:] else text)
    except ValueError:
        day = _iso_day(value)
        return datetime.combine(day, datetime.min.time(), tzinfo=tz) if day else None
    return moment.replace(tzinfo=tz) if moment.tzinfo is None else moment


def flag_partial(payload: Any, today: date) -> None:
    """Generic completeness warning from the window the server resolved (no preset names): a window that reaches
    today is still being written, so a like-for-like comparison needs the same cut-off on both sides. A multi-view
    result is checked part by part."""
    if not isinstance(payload, dict):
        return
    parts = [p for p in payload.get("parts") or [] if isinstance(p, dict)]
    for source in [payload, *parts]:
        prov = source.get("provenance") or {}
        end = _iso_day((prov.get("time_range") or {}).get("end"))
        if end is not None and end >= today and not prov.get("compare_cutoff"):  # the server already cut the comparison at the same hour
            payload.setdefault("warnings", []).append(
                f"Window ends {end}, which is not a complete day (today is {today}, data keeps arriving): treat it as partial; "
                "do not compare it with a full period or call it a drop without the same cut-off on both sides.")
            return


def _as_num(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _entity(row: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(the names of the label columns, their values) of one result row."""
    labels = {k: v for k, v in row.items() if isinstance(v, str) and _as_num(v) is None}
    return tuple(sorted(labels)), tuple(labels[k] for k in sorted(labels))


def flag_composed(payload: Any) -> None:
    """A multi-view result is several parts, each sorted and limited on its own, so each part holds a different set of
    entities. An entity absent from a part fell outside that part's top-N: its value there is unknown, not zero.
    Only parts that break down by the same dimensions are comparable; the others share no entities by design."""
    parts = payload.get("parts") if isinstance(payload, dict) else None
    if not isinstance(parts, list) or len(parts) < 2:
        return
    groups: dict[tuple[str, ...], list[set[tuple[str, ...]]]] = {}
    for part in parts:
        rows = [r for r in (part.get("rows") if isinstance(part, dict) else None) or [] if isinstance(r, dict)]
        entities = [_entity(r) for r in rows]
        if entities:
            groups.setdefault(entities[0][0], []).append({values for _, values in entities})
    partial: set[str] = set()
    total = 0
    for keysets in groups.values():
        if len(keysets) < 2:
            continue
        union = set().union(*keysets)
        total += len(union)
        partial |= {" / ".join(k) for k in union if any(k not in ks for ks in keysets)}
    if partial:
        ordered = sorted(partial)
        payload.setdefault("warnings", []).append(
            f"{len(partial)} of {total} entities are missing from at least one part (e.g. {'; '.join(ordered[:4])}): "
            "parts are ranked and limited separately, so their values in the other parts are unknown, NOT zero or absent. "
            "Re-query those entities with an equals filter before comparing or ranking them.")


# ---- evidence ---------------------------------------------------------------------------------
_EVIDENCE_IDENTITY = ("metric_id", "dimensions", "grain", "period_start", "period_end", "value", "unit", "source_query",
                      "period")


def _identity(payload: dict[str, Any]) -> str:
    return json.dumps({k: payload.get(k) for k in _EVIDENCE_IDENTITY}, sort_keys=True, default=str)


def _bucket_end(start: date, grain: str, range_end: date | None) -> date:
    if grain == "week":
        end = start + timedelta(days=6)
    elif grain in ("month", "quarter", "year"):
        months = {"month": 1, "quarter": 3, "year": 12}[grain]
        index = start.year * 12 + start.month - 1 + months
        end = date(index // 12, index % 12 + 1, 1) - timedelta(days=1)
    else:
        end = start
    return min(end, range_end) if range_end else end


def _evidence_grain(bucket: str | None, *, has_time: bool) -> str:
    """Map a Cube time-dimension suffix to an EvidenceArtifact grain."""
    if bucket in ("day", "week", "month", "hour"):
        return bucket
    if bucket in ("quarter", "year") or not has_time:
        return "none"
    return "day"  # unknown sub-day (e.g. minute) still keeps its stamp via period_start


def _rows_evidence(
    rows: list[dict[str, Any]], *, columns: list[str], measures: list[str], window: tuple[date, date] | None,
    args: dict[str, Any], deps: SelericDeps, currency: str, period: str = "current",
) -> list[EvidenceArtifact]:
    """One EvidenceArtifact per (row, measure): dimension columns become ``dimensions``, a time column the period.

    Hour buckets keep their timestamp (live 2026-10-21: collapsing every hour to midnight made the chart keep only
    the last hour of each day — spend ₹517 instead of the day total — and truncating evidence mid-series dropped days).
    """
    time_cols = [c for c in columns if "." in c]
    base_time = {c.split(".")[0] for c in time_cols}
    dim_cols = [c for c in columns if c not in measures and "." not in c and c not in base_time]
    tzinfo = deps.as_of.tzinfo or UTC
    out: list[EvidenceArtifact] = []
    for row in rows:
        bucket = moment = None
        for c in time_cols:
            moment = _iso_moment(row.get(c), tzinfo)
            if moment is not None:
                bucket = c.split(".", 1)[1]
                break
        if moment is None and window is None:
            continue
        if moment is not None:
            start_dt = moment.astimezone(tzinfo)
            if bucket == "hour":
                end_dt = start_dt  # one hour point; span checks treat hour grain as unconstrained
            else:
                day = start_dt.date()
                end_day = _bucket_end(day, bucket or "day", window[1] if window else None)
                start_dt = datetime.combine(day, datetime.min.time(), tzinfo=tzinfo)
                end_dt = datetime.combine(end_day, datetime.min.time(), tzinfo=tzinfo)
        else:
            start_dt = datetime.combine(window[0], datetime.min.time(), tzinfo=tzinfo)  # type: ignore[index]
            end_dt = datetime.combine(window[1], datetime.min.time(), tzinfo=tzinfo)  # type: ignore[index]
        dims = {c: str(row[c]) for c in dim_cols if row.get(c) not in (None, "")}
        grain = _evidence_grain(bucket, has_time=moment is not None)
        for m in measures:
            raw = row.get(m)
            value = _as_num(raw) if isinstance(raw, str) else (float(raw) if isinstance(raw, (int, float)) else None)
            unit = deps.catalogue.unit_for(m)
            out.append(EvidenceArtifact(
                metric_id=m, dimensions=dims, grain=grain,  # type: ignore[arg-type]
                as_of=deps.as_of, period_start=start_dt, period_end=end_dt,
                value=value, unit=currency if unit and unit == currency else (unit or currency or None),
                source_query=args, period=period))  # type: ignore[arg-type]
    return out


def _window(tr: Any) -> tuple[date, date] | None:
    if not isinstance(tr, dict):
        return None
    start, end = _iso_day(tr.get("start")), _iso_day(tr.get("end"))
    return (start, end) if start and end else None


def _flag_evidence_cut(payload: dict[str, Any], provenance: ArtifactProvenance, *, kept: int, total: int,
                       rows: list[dict[str, Any]], columns: list[str]) -> None:
    """The stored evidence of this query is only its first rows: say so to the model (a total computed over it covers
    less than the window — live 2026-10-21 MS3-99c235f372: 30 days of hourly rows, a 400-point cap, "last 30 days"
    answered from the first 2.75 days) and mark the artifacts so a script over them is warned too."""
    time_col = next((c for c in columns if "." in c), None)
    stamps = [str(r.get(time_col)) for r in rows if time_col and r.get(time_col)]
    covered = f" ({stamps[0][:16]} .. {stamps[-1][:16]})" if stamps else ""
    provenance.source_metadata["evidence_truncated"] = f"{kept} of {total} rows{covered}"
    payload.setdefault("warnings", []).append(
        f"Only the first {kept} of {total} rows are kept as evidence{covered}: a total, average or chart computed from "
        "evidence covers less than the window asked about. Re-query a coarser grain (day instead of hour), a shorter "
        "window or fewer measures before stating a period total.")


def write_evidence(payload: dict[str, Any], args: dict[str, Any], deps: SelericDeps, *, max_points: int | None = None) -> list[str]:
    """Persist a query result's rows (and compare rows) as evidence; returns the artifact ids."""
    prov = payload.get("provenance") or {}
    columns = [c for c in payload.get("columns") or [] if isinstance(c, str)]
    measures = [m for m in prov.get("metric_ids") or [] if m in columns]
    if not measures:
        return []
    currency = str(prov.get("currency") or "").strip()
    batches = [(payload.get("rows"), _window(prov.get("time_range")), "current")]
    if payload.get("compare_rows"):
        batches.append((payload.get("compare_rows"), _window(prov.get("compare_period")), "previous"))
    existing: dict[str, str] = {}
    for artifact in deps.artifact_store.list_for_mission(deps.mission_id):
        if artifact.artifact_type == "evidence" and isinstance(artifact.payload, dict):
            existing.setdefault(_identity(artifact.payload), artifact.id)
    provenance = ArtifactProvenance(query_version=str(payload.get("query_id") or ""), source_metadata={
        k: prov.get(k) for k in ("query_id", "metric_ids", "currency", "time_range", "compare_period", "freshness")})
    ids: list[str] = []
    for rows, window, period in batches:
        if not isinstance(rows, list):
            continue
        rows = [r for r in rows if isinstance(r, dict)]
        # Whole rows only: a cut through a row would give one measure more days than another.
        keep = max((max_points or MAX_EVIDENCE_ROWS) // len(measures), 1)
        if len(rows) > keep:
            _flag_evidence_cut(payload, provenance, kept=keep, total=len(rows), rows=rows[:keep], columns=columns)
            rows = rows[:keep]
        try:
            items = _rows_evidence(rows, columns=columns, measures=measures,
                                   window=window, args=args, deps=deps, currency=currency, period=period)
        except Exception:
            _log.warning("evidence_conversion_failed", exc_info=True)
            continue
        for ev in items:
            body = ev.model_dump(mode="json")
            key = _identity(body)
            if key in existing:
                ids.append(existing[key])
                continue
            art = deps.artifact_store.put(Artifact(
                workspace_id=deps.principal.workspace_id, artifact_type="evidence", payload=body,
                classification="factual", evidence_ids=[f"raw:{ev.metric_id}:{payload.get('query_id')}"],
                provenance=provenance, mission_id=deps.mission_id))
            existing[key] = art.id
            ids.append(art.id)
    return ids


def write_query_evidence(payload: dict[str, Any], args: dict[str, Any], deps: SelericDeps) -> dict[str, list[str]]:
    """Evidence ids per query id. A multi-view result holds one full sub-result per part (own query id, columns, rows):
    each part is written on its own, and the parent id maps to all of them, so any of those ids can be passed on."""
    parts = [p for p in payload.get("parts") or [] if isinstance(p, dict)]
    if not parts:
        return {str(payload["query_id"]): write_evidence(payload, args, deps)} if payload.get("query_id") else {}
    out: dict[str, list[str]] = {}
    for part in parts:
        ids = write_evidence(part, args, deps)
        if part.get("query_id"):
            out[str(part["query_id"])] = ids
    if payload.get("query_id"):
        out[str(payload["query_id"])] = list(dict.fromkeys(i for ids in out.values() for i in ids))
    return out


# ---- datasets (build_dataset_query) ------------------------------------------------------------
def _stamp(bucket: Any) -> str:
    """'2026-10-22 09:00' / '2026-10-22' as a Cube-style time bucket, which the evidence and table code read."""
    text = str(bucket or "")
    return f"{text[:10]}T{text[11:16]}:00.000" if len(text) >= 16 else f"{text[:10]}T00:00:00.000"


def dataset_as_query_payload(payload: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """A dataset (server schema + every row) in the shape of a metrics_query result, so evidence, tables, charts and
    run_python treat its handle like any query_id. Time column ``bucket.<grain>``; measures are the catalogue ids."""
    schema = [c for c in payload.get("columns") or [] if isinstance(c, dict)]
    prov = payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {}
    grain = str(payload.get("grain") or prov.get("grain") or "day")
    measures = [c["name"] for c in schema if c.get("role") == "measure"]
    dims = [c["name"] for c in schema if c.get("role") == "dimension"]
    time_col = f"bucket.{grain}"
    out_rows = [{time_col: _stamp(r.get("bucket")), **{k: r.get(k) for k in (*dims, *measures)}} for r in rows]
    handle = str(payload.get("handle") or payload.get("query_id") or "")
    return {
        "query_id": handle, "handle": handle, "dataset": True,
        "columns": [time_col, *dims, *measures], "schema": schema, "rows": out_rows,
        "row_count": len(out_rows), "grain": grain,
        "provenance": {**prov, "query_id": handle, "metric_ids": measures, "freshness": {"sources": prov.get("sources")}},
        "warnings": list(payload.get("warnings") or []),
        "notes": payload.get("notes"), "limitations": payload.get("limitations"), "stats": payload.get("stats"),
        "partial_bucket": payload.get("partial_bucket"),
    }


async def load_dataset(gateway: Any, handle: str, today: date | None) -> tuple[list[dict[str, Any]], dict[str, Any], str | None]:
    """(every row, the first page's envelope: schema columns and provenance, why it stopped or None) of a dataset."""
    rows: list[dict[str, Any]] = []
    first: dict[str, Any] = {}
    offset: int | None = 0
    while offset is not None:
        page, is_err = await _execute(gateway, DATASET_TOOL,
                                      {"action": "get", "handle": handle, "offset": offset, "limit": DATASET_PAGE}, today)
        if is_err or not isinstance(page, dict):
            return rows, first, compact(page)[:300]
        first = first or page
        rows.extend(r for r in page.get("rows") or [] if isinstance(r, dict))
        offset = page.get("next_offset")
    return rows, first, None


async def load_dataset_rows(gateway: Any, handle: str, today: date | None) -> tuple[list[dict[str, Any]], str | None]:
    rows, _, stopped = await load_dataset(gateway, handle, today)
    return rows, stopped


# ---- per-turn state and the tools --------------------------------------------------------------
@dataclass
class TurnState:
    """What one turn's tool calls leave behind: payloads for grounding, a digest for follow-ups, trace steps."""

    payloads: list[Any] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)  # {tool, args, query_id} per distinct successful call
    evidence_ids: list[str] = field(default_factory=list)
    by_query: dict[str, list[str]] = field(default_factory=dict)  # query_id -> evidence ids (what charts and tables read)
    steps: list[dict[str, Any]] = field(default_factory=list)
    parts: list[dict[str, Any]] = field(default_factory=list)  # structured answer parts (TABLE) the helpers attach
    tables: dict[str, dict[str, Any]] = field(default_factory=dict)  # table id -> TABLE content a helper offers (break_down_metric)
    prior_queries: list[str] = field(default_factory=list)  # 'query_id: tool args' of earlier turns
    prior_payloads: list[dict[str, Any]] = field(default_factory=list)  # earlier turns' kept results (compacted)
    prior_context: list[dict[str, Any]] = field(default_factory=list)  # the analysis records of the last turn
    context: list[dict[str, Any]] = field(default_factory=list)  # this turn's analysis records, one per tool call
    errors: list[str] = field(default_factory=list)
    seen: dict[str, asyncio.Future[Any]] = field(default_factory=dict)
    tool_calls: int = 0
    duplicates: int = 0
    tool_ms: int = 0
    today: date | None = None  # the mission calendar's today (its timezone); None falls back to IST


def tool_defs(raw_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Allowlisted tool definitions, sorted by name and cleaned once, so the prompt prefix stays byte-stable."""
    keep = {t["name"]: t for t in raw_tools if t.get("name") in ALLOW}
    return [{"name": n, "description": _desc(keep[n].get("description") or ""),
             "schema": _clean_schema(keep[n].get("inputSchema") or {"type": "object"})} for n in sorted(keep)]


def validate(defs: dict[str, dict[str, Any]], name: str, args: dict[str, Any]) -> str | None:
    d = defs.get(name)
    if d is None:
        return f"Unknown or non-allowlisted tool '{name}'. Allowed: {sorted(defs)}"
    errs = sorted(jsonschema.Draft202012Validator(d["schema"]).iter_errors(args), key=str)
    return "; ".join(f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errs[:5]) or filter_misuse(args)


async def _execute(gateway: Any, name: str, args: dict[str, Any], today: date | None = None) -> tuple[Any, bool]:
    try:
        payload = await asyncio.wait_for(
            gateway.call(agent_id="v3_agent", capability=f"seleric.{name}", arguments=args), TOOL_TIMEOUT_S)
    except TimeoutError:
        return {"error": f"Tool timed out after {TOOL_TIMEOUT_S:g}s"}, True
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}, True
    is_err = isinstance(payload, dict) and "error" in payload
    if not is_err and isinstance(payload, dict):
        flag_partial(payload, today or ist_today())
        flag_composed(payload)
        relabel_undated(payload)
    return payload, is_err


async def _take_dataset(ctx: RunContext[SelericDeps], gateway: Any, state: TurnState, args: dict[str, Any],
                        payload: dict[str, Any]) -> dict[str, Any] | None:
    """A dataset the turn has not loaded yet: a build (or a saved slice), or a get on a handle from an earlier turn.
    Load every row, record it as evidence under its handle and as a fetched result, and return what the model sees:
    a build shows schema, preview, stats and notes (not the rows); a get shows the page it asked for."""
    handle = str(payload["handle"])
    rows, first, stopped = await load_dataset(gateway, handle, state.today)
    is_build = "preview" in payload
    base = payload if is_build else {**first, "handle": handle, "query_id": handle, "row_count": first.get("row_count", len(rows))}
    full = dataset_as_query_payload(base, rows)
    if stopped:
        full.setdefault("warnings", []).append(f"Only {len(rows)} of {base.get('row_count')} rows could be loaded ({stopped}).")
    state.payloads.append(full)
    state.evidence.append({"tool": DATASET_TOOL, "args": args, "query_id": handle})
    ids = write_evidence(full, args, ctx.deps, max_points=MAX_DATASET_EVIDENCE)
    state.evidence_ids.extend(i for i in ids if i not in state.evidence_ids)
    state.by_query[handle] = ids
    note = (f"All {len(rows)} rows are loaded: pass query_id={handle!r} to run_python, show_chart or show_table. "
            "Quote the notes, the latest complete bucket per source and the limitations. A null cell is missing data, not zero.")
    if is_build:
        seen = {k: v for k, v in payload.items() if k not in ("provenance", "how_to_use")}
        seen["rows_loaded"] = len(rows)
    else:
        seen = dict(payload)
    seen["agent_note"] = note
    if full.get("warnings"):
        seen["warnings"] = [*seen.get("warnings", []), *full["warnings"]]
    return seen


def _takes_date_axis(definition: dict[str, Any] | None, args: dict[str, Any]) -> bool:
    """The tool's published schema has a ``date_axis`` and this call builds or queries (not a dataset get / delete)."""
    props = ((definition or {}).get("schema") or {}).get("properties") or {}
    return "date_axis" in props and args.get("action", "build") == "build"


def build_mcp_tools(gateway: Any, defs_list: list[dict[str, Any]], state: TurnState) -> list[Tool[SelericDeps]]:
    """One ``Tool`` per allowlisted MCP tool, bound to this turn's state."""
    defs = {d["name"]: d for d in defs_list}

    def make(name: str) -> Any:
        async def call(ctx: RunContext[SelericDeps], **raw_args: Any) -> str:
            args = {k: v for k, v in raw_args.items() if v is not None}  # some models emit explicit nulls
            if _takes_date_axis(defs.get(name), args):
                args = with_date_axis(args, ctx.deps.date_axis, ctx.deps.catalogue)
            started = time.perf_counter()
            err = validate(defs, name, args)
            if err:
                state.steps.append({"kind": "tool_call", "tool": name, "args": args})
                state.steps.append({"kind": "tool_return", "tool": name, "ok": False, "result": f"invalid arguments: {err}"})
                return json.dumps({"error": f"Invalid arguments: {err}", "schema": defs[name]["schema"]}, default=str)
            key = name + json.dumps(args, sort_keys=True, default=str)
            dup = key in state.seen
            if not dup:
                state.tool_calls += 1  # a repeat of a call already made costs nothing: it is served from the turn's cache
                state.seen[key] = asyncio.ensure_future(_execute(gateway, name, args, state.today))
            else:
                state.duplicates += 1
            payload, is_err = await state.seen[key]
            ms = int((time.perf_counter() - started) * 1000)
            state.tool_ms += ms
            if not dup:
                state.context.append(analysis_context.entry(name, args, payload, ok=not is_err))
                if is_err:
                    state.seen.pop(key, None)  # a failure is not cached: the model's retry must run again
                    state.errors.append(f"{name}: {compact(payload)[:300]}")
                elif (name == DATASET_TOOL and isinstance(payload, dict) and payload.get("handle")
                      and ("preview" in payload or (args.get("action") == "get" and payload["handle"] not in state.by_query))):
                    shown = await _take_dataset(ctx, gateway, state, args, payload)
                    if shown is not None:
                        payload = shown
                else:
                    state.payloads.append(payload)
                    qid = payload.get("query_id") if isinstance(payload, dict) else None
                    state.evidence.append({"tool": name, "args": args, "query_id": qid})
                    if name in QUERY_TOOLS and isinstance(payload, dict):
                        by_query = write_query_evidence(payload, args, ctx.deps)
                        for ids in by_query.values():
                            state.evidence_ids.extend(i for i in ids if i not in state.evidence_ids)
                        state.by_query.update(by_query)
            body = compact(payload)
            state.steps.append({"kind": "tool_call", "tool": name, "args": args, "duplicate": dup})
            state.steps.append({"kind": "tool_return", "tool": name, "ok": not is_err, "ms": ms, "chars": len(body),
                                "query_id": payload.get("query_id") if isinstance(payload, dict) else None,
                                "result": body})  # whole return (already capped by compact): the UI cuts it for display
            return body

        return call

    return [
        Tool.from_schema(make(n), name=n, description=d["description"], json_schema=d["schema"], takes_ctx=True)
        for n, d in defs.items()
    ]
