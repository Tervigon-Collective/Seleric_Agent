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
from typing import Any
from zoneinfo import ZoneInfo

import jsonschema
from pydantic_ai import RunContext
from pydantic_ai.tools import Tool

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance

_log = logging.getLogger("seleric.agent.mcp_toolset")

# Read-only allowlist. Server annotations carry no readOnlyHint, so we never trust them.
ALLOW: tuple[str, ...] = (
    "catalogue_search_metrics", "catalogue_get_metrics", "catalogue_get_metric", "catalogue_list_dimensions",
    "catalogue_resolve_dimension", "catalogue_resolve_term", "catalogue_resolve_concept", "catalogue_resolve_values",
    "catalogue_resolve_brand", "catalogue_list_brands", "catalogue_get_ontology", "catalogue_related_metrics",
    "modules_list", "metrics_query", "metrics_drilldown", "insights_explain",
)
QUERY_TOOLS = frozenset({"metrics_query", "metrics_drilldown"})

MAX_ROWS = int(os.getenv("AGENT_MAX_ROWS", "60"))  # list cap inside a tool result
MAX_RESULT_CHARS = int(os.getenv("AGENT_MAX_RESULT_CHARS", "14000"))
TOOL_TIMEOUT_S = float(os.getenv("AGENT_TOOL_TIMEOUT_S", "45"))
MAX_EVIDENCE_ROWS = 400  # evidence rows written per query result

IST = ZoneInfo("Asia/Kolkata")

DROP_KEYS = {"cube_query", "semantic", "catalogue_version", "generated_at", "cube_sql", "clickhouse_trace",
             "lookup_sql", "catalogue_sha", "serve_objects", "openmetadata"}
VALUELESS_OPS = ("set", "notSet")  # "is not null" / "is null": the server ignores any values and returns the unfiltered result


# ---- results: compact without losing numbers -------------------------------------------------
def _shrink(o: Any, cap: int) -> Any:
    if isinstance(o, dict):  # explicit nulls stay: a NULL cell (e.g. no LPV for Google) is not the same as "not fetched"
        return {k: _shrink(v, cap) for k, v in o.items() if k not in DROP_KEYS and v not in ([], {}, "")}
    if isinstance(o, list):
        out = [_shrink(v, cap) for v in o[:cap]]
        return out + [f"... {len(o) - cap} more items truncated (narrow the query or sort+limit)"] if len(o) > cap else out
    return o


def compact(payload: Any, *, max_rows: int = MAX_ROWS, max_chars: int = MAX_RESULT_CHARS) -> str:
    """Drop plumbing, cap long lists (explicit marker), never cut through a number."""
    cap = max_rows
    while True:
        s = json.dumps(_shrink(payload, cap), separators=(",", ":"), ensure_ascii=False, default=str)
        if len(s) <= max_chars or cap <= 5:
            break
        cap //= 2
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


def flag_partial(payload: Any, today: date) -> None:
    """Generic completeness warning from the window the server resolved (no preset names): a window that reaches
    today is still being written, so a like-for-like comparison needs the same cut-off on both sides."""
    tr = ((payload.get("provenance") or {}).get("time_range") or {}) if isinstance(payload, dict) else {}
    try:
        end = date.fromisoformat(str(tr.get("end")))
    except ValueError:
        return
    if end >= today:
        payload.setdefault("warnings", []).append(
            f"Window ends {end}, which is not a complete day (today is {today}, data keeps arriving): treat it as partial; "
            "do not compare it with a full period or call it a drop without the same cut-off on both sides.")


def _as_num(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def flag_composed(payload: Any) -> None:
    """A multi-view result is several parts, each sorted and limited on its own, so each part holds a different set of
    entities. An entity absent from a part fell outside that part's top-N: its value there is unknown, not zero."""
    parts = payload.get("parts") if isinstance(payload, dict) else None
    if not isinstance(parts, list) or len(parts) < 2:
        return
    keysets = []
    for part in parts:
        rows = part.get("rows") if isinstance(part, dict) else None
        keysets.append({tuple(str(v) for v in r.values() if isinstance(v, str) and _as_num(v) is None)
                        for r in rows or [] if isinstance(r, dict)})
    union = set().union(*keysets)
    partial = sorted({" / ".join(k) for k in union if any(k not in ks for ks in keysets)})
    if partial:
        payload.setdefault("warnings", []).append(
            f"{len(partial)} of {len(union)} entities are missing from at least one part (e.g. {'; '.join(partial[:4])}): "
            "parts are ranked and limited separately, so their values in the other parts are unknown, NOT zero or absent. "
            "Re-query those entities with an equals filter before comparing or ranking them.")


# ---- evidence ---------------------------------------------------------------------------------
_EVIDENCE_IDENTITY = ("metric_id", "dimensions", "grain", "period_start", "period_end", "value", "unit", "source_query")


def _identity(payload: dict[str, Any]) -> str:
    return json.dumps({k: payload.get(k) for k in _EVIDENCE_IDENTITY}, sort_keys=True, default=str)


def _iso_day(value: Any) -> date | None:
    text = str(value or "")
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    return None


def _bucket_end(start: date, grain: str, range_end: date | None) -> date:
    if grain == "week":
        end = start + timedelta(days=6)
    elif grain == "month":
        nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = nxt - timedelta(days=1)
    else:
        end = start
    return min(end, range_end) if range_end else end


def _rows_evidence(
    rows: list[dict[str, Any]], *, columns: list[str], measures: list[str], window: tuple[date, date] | None,
    args: dict[str, Any], deps: SelericDeps, currency: str,
) -> list[EvidenceArtifact]:
    """One EvidenceArtifact per (row, measure): dimension columns become ``dimensions``, a time column the period."""
    time_cols = [c for c in columns if "." in c]
    base_time = {c.split(".")[0] for c in time_cols}
    dim_cols = [c for c in columns if c not in measures and "." not in c and c not in base_time]
    tzinfo = deps.as_of.tzinfo or UTC
    out: list[EvidenceArtifact] = []
    for row in rows:
        day = grain = None
        for c in time_cols:
            day = _iso_day(row.get(c))
            if day:
                grain = c.split(".", 1)[1]
                break
        if day is None and window is None:
            continue
        start = day or window[0]  # type: ignore[index]
        end = _bucket_end(day, grain or "day", window[1] if window else None) if day else window[1]  # type: ignore[index]
        dims = {c: str(row[c]) for c in dim_cols if row.get(c) not in (None, "")}
        for m in measures:
            raw = row.get(m)
            value = _as_num(raw) if isinstance(raw, str) else (float(raw) if isinstance(raw, (int, float)) else None)
            unit = deps.catalogue.unit_for(m)
            out.append(EvidenceArtifact(
                metric_id=m, dimensions=dims, grain=grain if grain in ("day", "week", "month") else ("day" if day else "none"),  # type: ignore[arg-type]
                as_of=deps.as_of,
                period_start=datetime.combine(start, datetime.min.time(), tzinfo=tzinfo),
                period_end=datetime.combine(end, datetime.min.time(), tzinfo=tzinfo),
                value=value, unit=currency if unit and unit == currency else (unit or currency or None),
                source_query=args))
            if len(out) >= MAX_EVIDENCE_ROWS:
                return out
    return out


def _window(tr: Any) -> tuple[date, date] | None:
    if not isinstance(tr, dict):
        return None
    start, end = _iso_day(tr.get("start")), _iso_day(tr.get("end"))
    return (start, end) if start and end else None


def write_evidence(payload: dict[str, Any], args: dict[str, Any], deps: SelericDeps) -> list[str]:
    """Persist a query result's rows (and compare rows) as evidence; returns the artifact ids."""
    prov = payload.get("provenance") or {}
    columns = [c for c in payload.get("columns") or [] if isinstance(c, str)]
    measures = [m for m in prov.get("metric_ids") or [] if m in columns]
    if not measures:
        return []
    currency = str(prov.get("currency") or "").strip()
    batches = [(payload.get("rows"), _window(prov.get("time_range")))]
    if payload.get("compare_rows"):
        batches.append((payload.get("compare_rows"), _window(prov.get("compare_period"))))
    existing: dict[str, str] = {}
    for artifact in deps.artifact_store.list_for_mission(deps.mission_id):
        if artifact.artifact_type == "evidence" and isinstance(artifact.payload, dict):
            existing.setdefault(_identity(artifact.payload), artifact.id)
    provenance = ArtifactProvenance(query_version=str(payload.get("query_id") or ""), source_metadata={
        k: prov.get(k) for k in ("query_id", "metric_ids", "currency", "time_range", "compare_period", "freshness")})
    ids: list[str] = []
    for rows, window in batches:
        if not isinstance(rows, list):
            continue
        try:
            items = _rows_evidence([r for r in rows if isinstance(r, dict)], columns=columns, measures=measures,
                                   window=window, args=args, deps=deps, currency=currency)
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


# ---- per-turn state and the tools --------------------------------------------------------------
@dataclass
class TurnState:
    """What one turn's tool calls leave behind: payloads for grounding, a digest for follow-ups, trace steps."""

    payloads: list[Any] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)  # {tool, args, query_id} per distinct successful call
    evidence_ids: list[str] = field(default_factory=list)
    by_query: dict[str, list[str]] = field(default_factory=dict)  # query_id -> evidence ids (what charts and tables read)
    steps: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    seen: dict[str, asyncio.Future[Any]] = field(default_factory=dict)
    tool_calls: int = 0
    duplicates: int = 0
    tool_ms: int = 0


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


async def _execute(gateway: Any, name: str, args: dict[str, Any]) -> tuple[Any, bool]:
    try:
        payload = await asyncio.wait_for(
            gateway.call(agent_id="v3_agent", capability=f"seleric.{name}", arguments=args), TOOL_TIMEOUT_S)
    except TimeoutError:
        return {"error": f"Tool timed out after {TOOL_TIMEOUT_S:g}s"}, True
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}, True
    is_err = isinstance(payload, dict) and "error" in payload
    if not is_err and isinstance(payload, dict):
        flag_partial(payload, ist_today())
        flag_composed(payload)
    return payload, is_err


def build_mcp_tools(gateway: Any, defs_list: list[dict[str, Any]], state: TurnState) -> list[Tool[SelericDeps]]:
    """One ``Tool`` per allowlisted MCP tool, bound to this turn's state."""
    defs = {d["name"]: d for d in defs_list}

    def make(name: str) -> Any:
        async def call(ctx: RunContext[SelericDeps], **raw_args: Any) -> str:
            args = {k: v for k, v in raw_args.items() if v is not None}  # some models emit explicit nulls
            started = time.perf_counter()
            err = validate(defs, name, args)
            if err:
                state.steps.append({"kind": "tool_call", "tool": name, "args": args})
                state.steps.append({"kind": "tool_return", "tool": name, "ok": False, "result": f"invalid arguments: {err}"})
                return json.dumps({"error": f"Invalid arguments: {err}", "schema": defs[name]["schema"]}, default=str)
            key = name + json.dumps(args, sort_keys=True, default=str)
            dup = key in state.seen
            state.tool_calls += 1
            if not dup:
                state.seen[key] = asyncio.ensure_future(_execute(gateway, name, args))
            else:
                state.duplicates += 1
            payload, is_err = await state.seen[key]
            ms = int((time.perf_counter() - started) * 1000)
            state.tool_ms += ms
            evidence_ids: list[str] = []
            if not dup:
                if is_err:
                    state.errors.append(f"{name}: {compact(payload)[:300]}")
                else:
                    state.payloads.append(payload)
                    qid = payload.get("query_id") if isinstance(payload, dict) else None
                    state.evidence.append({"tool": name, "args": args, "query_id": qid})
                    if name in QUERY_TOOLS and isinstance(payload, dict):
                        evidence_ids = write_evidence(payload, args, ctx.deps)
                        state.evidence_ids.extend(i for i in evidence_ids if i not in state.evidence_ids)
                        if qid:
                            state.by_query[str(qid)] = evidence_ids
            body = compact(payload)
            state.steps.append({"kind": "tool_call", "tool": name, "args": args, "duplicate": dup})
            state.steps.append({"kind": "tool_return", "tool": name, "ok": not is_err, "ms": ms, "chars": len(body),
                                "query_id": payload.get("query_id") if isinstance(payload, dict) else None,
                                "result": body[:2000]})
            return body

        return call

    return [
        Tool.from_schema(make(n), name=n, description=d["description"], json_schema=d["schema"], takes_ctx=True)
        for n, d in defs.items()
    ]
