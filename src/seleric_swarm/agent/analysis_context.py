"""What one turn analysed, one record per tool call (MCP or local), kept with the turn and handed to the next as data.

A follow-up ("which channel pulled in the most of that?", "is COD the reason?") is about the metrics, period, filters
and rows of the turn before. Only calls with a query id used to be remembered, as a prose line; a diagnosis or a
breakdown left nothing, so the next turn asked again or fell back to yesterday.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

MAX_ENTRIES = 20
MAX_NOTES = 6


def _day(v: Any) -> str | None:
    if v is None or v == "None":
        return None
    return v.date().isoformat() if isinstance(v, datetime) else v.isoformat() if isinstance(v, date) else str(v)[:10]


def _window(args: dict[str, Any], start: str, end: str) -> dict[str, Any] | None:
    s, e = _day(args.get(start)), _day(args.get(end))
    return {"start": s or e, "end": e or s} if (s or e) else None


def _list(v: Any) -> list[str]:
    if v in (None, "None", "", []):
        return []
    return [str(x) for x in v] if isinstance(v, (list, tuple)) else [str(v)]


def entry(tool: str, args: dict[str, Any], payload: Any, *, ok: bool) -> dict[str, Any]:
    """The record of one call: what it asked (metrics, window, comparison, filters, dimensions) and what came back
    (result id, row count, the tool's notes). Read from the tools' own argument names; empty fields are left out."""
    p = payload if isinstance(payload, dict) else {}
    tr = args.get("time_range")
    window = ({k: _day(v) if k != "preset" else v for k, v in tr.items()} if isinstance(tr, dict)
              else _window(args, "event_start", "event_end") or _window(args, "period_start", "period_end"))
    compare = args.get("compare_period") or _window(args, "compare_start", "compare_end")
    rows = p.get("rows") if isinstance(p.get("rows"), list) else None
    parts = [x.get("query_id") for x in p.get("parts") or [] if isinstance(x, dict) and x.get("query_id")]
    notes = [str(w) for w in p.get("warnings") or [] if "brand filter" not in str(w)][:MAX_NOTES]
    out = {
        "tool": tool,
        "ok": ok,
        "query_id": p.get("query_id") or p.get("handle"),
        "part_query_ids": parts,
        "metrics": _list(args.get("measures")) or _list(args.get("metric_id")),
        "drivers": _list(args.get("drivers")),
        "window": window,
        "compare": compare,
        "granularity": args.get("granularity") or args.get("grain"),
        "dimensions": _list(args.get("dimensions")) or _list(args.get("by")) or _list(args.get("target_dimensions")),
        "filters": args.get("filters") if args.get("filters") not in (None, "None", [], {}) else None,
        "parent_query_id": args.get("parent_query_id"),
        "row_count": len(rows) if rows is not None else None,
        "notes": notes,
        "error": str(p.get("error"))[:300] if not ok and p.get("error") else None,
    }
    return {k: v for k, v in out.items() if v not in (None, [], {}, "")}


def last_window(context: list[dict[str, Any]]) -> tuple[date, date] | None:
    """The period the conversation was last looking at: the newest successful call that names a dated window."""
    for e in reversed(context):
        w = e.get("window") if e.get("ok") else None
        if isinstance(w, dict) and w.get("start") and w.get("end"):
            try:
                return date.fromisoformat(w["start"]), date.fromisoformat(w["end"])
            except ValueError:
                continue
    return None
