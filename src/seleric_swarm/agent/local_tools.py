"""The loop's local tools: analysis helpers the model calls when a question needs them (never a forced stage).

* Existing toolsets, registered as they are: ``diagnose_metric_change``, ``break_down_metric``, ``forecast_metrics``,
  ``explore_data`` (each fetches its own data and records evidence).
* ``run_python`` and ``show_chart`` take the ``query_id`` of a fetched result instead of evidence ids: the model sees
  query ids in every MCP result, the evidence rows behind them stay server-side.
* ``show_table`` attaches a full table of a fetched result next to the answer (a TABLE part).
* ``audit_entities`` computes the derived facts of a performance analysis (return on spend, spend with no orders, best/worst
  with enough orders) from fetched results, in code.
* ``business_snapshot`` returns the latest hourly business-health snapshot as data, when it is fresh.
* ``draft_mom_email`` / ``draft_ad_hoc_email`` / ``send_email`` (agent/email_tools.py): draft a session email for the
  user's approval, then send only the version they approved.
* ``escalate`` raises the reasoning effort for the rest of the turn.
"""

from __future__ import annotations

import functools
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import RunContext

from seleric_swarm.agent import analysis_context, present
from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.email_tools import build_email_tools
from seleric_swarm.agent.grounding import answer_numbers
from seleric_swarm.agent.mcp_toolset import TurnState
from seleric_swarm.agent.model import LOOP_ESCALATED_EFFORT, LOOP_WRITEUP_EFFORT, set_loop_effort
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.mail import evidence as mail_evidence
from seleric_swarm.toolsets import (
    analytics,
    composition,
    diagnosis,
    entity_audit,
    exploration,
    forecasting,
    sandbox,
)

_log = logging.getLogger("seleric.agent.local_tools")


MAX_TABLE_ROWS = 200


def query_id_of(payload: dict[str, Any]) -> str:
    prov = payload.get("provenance")
    return str(payload.get("query_id") or (prov.get("query_id") if isinstance(prov, dict) else "") or "")


def _whole(payload: dict[str, Any]) -> bool:
    """No list in the kept result was cut (compact_value marks a cut list with a '... N more items' string)."""
    return not any(isinstance(x, str) and x.startswith("... ") for v in (payload, *(payload.get("parts") or []))
                   if isinstance(v, dict) for rows in v.values() if isinstance(rows, list) for x in rows)


def _payload_for(state: TurnState, query_id: str) -> dict[str, Any] | None:
    """The fetched result with this query id: a whole result, or one part of a multi-view result. A result of an
    earlier turn is used when it was kept whole (its rows were not cut)."""
    earlier = [p for p in state.prior_payloads if isinstance(p, dict) and _whole(p)]
    for p in [*state.payloads, *earlier]:
        if not isinstance(p, dict):
            continue
        if query_id_of(p) == query_id:
            return p
        for part in p.get("parts") or []:
            if isinstance(part, dict) and query_id_of(part) == query_id:
                return part
    return None


def _no_table(state: TurnState, query_id: str, *, found: bool) -> str:
    """Why ``show_table`` has nothing for this id, and what to do next (a dead end made the model give up on the table)."""
    if found:
        return f"The result {query_id!r} has no rows to tabulate. Show its figures in the text, or fetch the breakdown you want with metrics_query."
    earlier = next((q[len(query_id) + 2:] for q in state.prior_queries if q.startswith(f"{query_id}: ")), None)
    if earlier is not None:
        return (f"{query_id!r} is from an earlier turn and its rows were not kept. Run that query again this turn ({earlier}), "
                "then call show_table with the new query_id.")
    return (f"No fetched result has query_id {query_id!r}. Fetched this turn: {sorted(state.by_query) or 'nothing yet'}. "
            "Fetch the rows with metrics_query first, or pass the table id that break_down_metric printed for its lines.")


def _rehydrate(deps: SelericDeps | None, state: TurnState, query_ids: list[str] | None) -> None:
    """Query ids from earlier turns of the thread: their rows are stored as evidence under the query id (a multi-view
    parent under its parts' ids). Map them so a follow-up computes on, charts or tabulates them without re-fetching."""
    missing = [q for q in query_ids or [] if q not in state.by_query]
    if not missing or not callable(getattr(getattr(deps, "artifact_store", None), "list_for_mission", None)):
        return
    try:
        wanted = {q: {q, *mail_evidence._part_ids(deps, q)} for q in missing}
        for mission in mail_evidence._mission_ids(deps):
            for a in deps.artifact_store.list_for_mission(mission):
                if a.artifact_type != "evidence":
                    continue
                for q, versions in wanted.items():
                    if a.provenance.query_version in versions:
                        state.by_query.setdefault(q, []).append(a.id)
    except Exception:  # a store that cannot be read leaves the 'not fetched' answer in place
        _log.warning("rehydrate_failed", exc_info=True)


def _evidence_for(state: TurnState, query_ids: list[str] | None, evidence_ids: list[str] | None = None) -> list[str] | None:
    """Evidence ids behind the named query ids (plus any given directly); None means 'everything this mission fetched'."""
    ids: list[str] = list(evidence_ids or [])
    for qid in query_ids or []:
        ids.extend(i for i in state.by_query.get(qid, []) if i not in ids)
    return ids or None


class ToolView(BaseModel):
    """What the model sees of a helper's result: the text written for it, not the server-side bookkeeping.

    A diagnosis returns ~80k characters (its summary is 6k; ``provenance`` holds a 65k structured report and
    ``artifact_ids`` a list per evidence row). Sent back on every later step that was 20k tokens of context per call.
    """

    success: bool
    summary: str
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None
    evidence_rows: int = 0


def _view(result: Any) -> Any:
    if not isinstance(result, ToolResult):
        return result
    return ToolView(success=result.success, summary=result.summary, warnings=list(result.warnings),
                    error_code=result.error_code, evidence_rows=len(result.artifact_ids))


def _remember_figures(state: TurnState, result: Any) -> None:
    """A helper's figures can be quoted: its summary's numbers join the turn's grounding pool (quote-only)."""
    if isinstance(result, dict):  # business_snapshot already joined the pool as structured data
        return
    summary = getattr(result, "summary", "") or ""
    figures = [n["value"] for n in answer_numbers(summary)]
    if figures:
        state.payloads.append({"helper_figures": figures})


def _offer_table(state: TurnState, result: Any) -> Any:
    """A helper that computed its lines as a table (break_down_metric) is offered to ``show_table`` under its finding id."""
    source = getattr(getattr(result, "provenance", None), "source_metadata", None)
    table = source.get("table") if isinstance(source, dict) else None
    table_id = str(source.get("finding_id") or "") if isinstance(source, dict) else ""
    if not (getattr(result, "success", False) and isinstance(table, dict) and table.get("rows") and table_id):
        return result
    state.tables[table_id] = table
    return result.model_copy(update={"summary": f"{result.summary}\n\nThese lines can be shown as a table next to the answer: "
                                                f"show_table(query_id={table_id!r}). Do not paste them into the text as well."})


def _tracked(state: TurnState, fn: Any, *, raises_effort: bool = False, quotable: bool = True) -> Any:
    """Wrap a local tool: time it, record it in the turn's trace like an MCP call, keep its figures quotable."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        t0 = time.perf_counter()
        result = await fn(*args, **kwargs)
        ms = int((time.perf_counter() - t0) * 1000)
        failed = getattr(result, "success", True) is False or (isinstance(result, dict) and result.get("available") is False)
        summary = getattr(result, "summary", None)
        if summary is None:
            summary = result if isinstance(result, str) else json.dumps(result, default=str)
        state.context.append(analysis_context.entry(fn.__name__, kwargs, result if isinstance(result, dict) else None, ok=not failed))
        state.steps.append({"kind": "tool_call", "tool": fn.__name__, "args": {k: str(v)[:200] for k, v in kwargs.items()}})
        step = {"kind": "tool_return", "tool": fn.__name__, "ok": not failed, "ms": ms, "result": str(summary)}
        source = getattr(getattr(result, "provenance", None), "source_metadata", None)
        if isinstance(source, dict) and isinstance(source.get("phase_ms"), dict):
            step["phase_ms"] = source["phase_ms"]
        state.steps.append(step)
        if failed:  # MCP failures are recorded by the toolset; a helper that returns ok:false must show up the same way
            state.errors.append(f"{fn.__name__}: {str(summary)[:300]}")
        if quotable:  # an email draft's text must never become evidence for later answers
            _remember_figures(state, result)
        result = _offer_table(state, result)
        if raises_effort and not failed:  # the engine did the causal reasoning; a refusal leaves real reasoning to do
            set_loop_effort(LOOP_WRITEUP_EFFORT)
        return _view(result)

    wrapper.__name__ = wrapper.__qualname__ = fn.__name__
    return wrapper


def build_local_tools(state: TurnState, *, snapshot_loader: Any = None, is_cancelled: Callable[[], bool] | None = None,
                      figures: Callable[[], list[Any]] | None = None) -> list[Any]:
    """Tools bound to this turn's state."""

    async def run_python(
        ctx: RunContext[SelericDeps], code: str, query_ids: list[str] | None = None, purpose: str = ""
    ) -> ToolResult:
        """Run a short python script over results you already fetched: custom ratios, rankings, multi-step arithmetic.

        Pass the ``query_id`` of each result to read (omitted: everything fetched this turn). It never fetches data.
        Sum or roll up rows here, never in the answer's prose: a total computed here is recorded and can be cited.

        The script is plain top-level code with exactly two data names, ``evidence`` and ``WORKDIR``. No variable is
        named after a query id and there is no ``query_results``; no ``return``, no function wrapper.
          - ``evidence``: a LIST of dicts, one per fetched value. Each row has metric_id, value, unit, grain,
            period ("current" or "previous": a compare_period result brings the previous window as its own rows),
            period_start, period_end, query_id and ``dimensions`` (a dict); every dimension is also a key of the row.
            Split windows with ``e["period"]``, never by comparing period_start to a string.
          - ``WORKDIR``: a directory for intermediate files; stdlib math/statistics/json/collections (+ numpy/pandas).
        A complete script (rank campaigns by spend, share of the total)::

            rows = [e for e in evidence if e["metric_id"] == "ad_spend" and e["query_id"] == "q_ab12"]
            total = sum(r["value"] for r in rows)
            result = [{"campaign": r["campaign_name"], "share_pct": 100 * r["value"] / total}
                      for r in sorted(rows, key=lambda r: -r["value"])[:5]]

        Read every figure from ``evidence``: a script that types a fetched value in as a literal is refused. Assign
        what you computed to ``result`` (a number, dict or list): printing is not enough. It is returned in full and
        every number in it is recorded so it can be cited. No network, no eval/exec, no imports beyond the stdlib set
        above. A code error returns the traceback and the first row: fix it and retry.
        """
        _rehydrate(getattr(ctx, "deps", None), state, query_ids)
        ids = _evidence_for(state, query_ids)
        if query_ids and ids is None:
            known = sorted(state.by_query)
            return ToolResult(
                success=False, retryable=True, error_code="INSUFFICIENT_EVIDENCE",
                summary=f"No fetched result has query_id {query_ids}. Fetched this turn: {known or 'nothing yet'}.")
        return await sandbox.run_python(ctx, code, ids, purpose)

    async def show_chart(
        ctx: RunContext[SelericDeps],
        query_ids: list[str],
        intent: str,
        title: str = "Visualization",
        chart_type: str | None = None,
    ) -> ToolResult:
        """Show a chart of fetched results. Use for trends over time, more than 3 categories, compositions or
        distributions; not for a single number.

        ``query_ids`` are the results to plot; ``intent`` says what the chart should show. ``chart_type`` (omit to
        let the data decide): line/area trend, horizontal_bar ranking or top-N, bar few categories, stacked_bar
        parts over time, donut/treemap share of a whole, grouped_bar or scatter two measures per entity, combo one
        volume + one rate over time, funnel stages, waterfall signed steps, heatmap, radar.
        """
        _rehydrate(getattr(ctx, "deps", None), state, query_ids)
        return await analytics.generate_visualization(
            ctx, _evidence_for(state, query_ids) or [], intent, title, chart_type
        )

    async def show_table(ctx: RunContext[SelericDeps], query_id: str, caption: str = "") -> ToolResult:
        """Show the full rows of one fetched result as a sortable table next to the answer. Use it for a long breakdown
        (many campaigns, products, days) instead of pasting every row into the text; keep the text to the finding and
        the top rows. A result with several views is shown as one table joined on the shared entity. ``query_id`` is
        the result to show; ``caption`` says what the table is."""
        if (offered := state.tables.get(query_id)) is not None:  # the lines of a break_down_metric
            content = {**offered, "caption": "; ".join(x for x in (caption, offered.get("caption")) if x), "query_id": query_id}
            state.parts.append({"type": "TABLE", "content": content})
            return ToolResult(success=True, summary=f"Table of {len(content['rows'])} lines attached to your answer. "
                              "Do not repeat the full table in the text.")
        payload = _payload_for(state, query_id)
        call_args = present.call_args_for(state, query_id)
        frame = present.frame_for(present.sources_of(payload), call_args)
        if frame is None or not frame.rows:
            return ToolResult(success=False, summary=_no_table(state, query_id, found=payload is not None),
                              error_code="INSUFFICIENT_EVIDENCE", retryable=True)
        present.apply_call_hints(frame, call_args)  # the query's measure order and ranking
        content = present.table_content(ctx.deps, frame, query_id=query_id, caption=caption, limit=MAX_TABLE_ROWS)
        state.parts.append({"type": "TABLE", "content": content})
        more = f" (first {len(content['rows'])} of {content['total_rows']} rows)" if content["total_rows"] > len(content["rows"]) else ""
        left_out = (f" {frame.omitted} more row(s) exist in the other views and are NOT in this table: do not say they have no data."
                    if frame.omitted else "")
        return ToolResult(success=True, summary=f"Table of {len(content['rows'])} rows attached to your answer{more}. "
                          f"Do not repeat the full table in the text.{left_out}")

    async def audit_entities(ctx: RunContext[SelericDeps], query_ids: list[str]) -> ToolResult:
        """The derived facts of a performance analysis, computed from results you already fetched: the portfolio's return
        on spend and spend per order, the share of spend on entities with no orders, concentration, the best and worst
        entities among those with enough orders to say so, and attention (clicks, hook rate) that does not convert.

        Use it for "analyse / which campaigns, products or channels work or waste money" questions, after fetching, by
        the entity (campaign_name, product_title, ...): ad spend, orders and revenue (and CTR / hook rate when asked).
        ``query_ids`` are those results; a result with several views is joined on the shared entity, and an entity a view
        has no row for stays in (no orders recorded is a finding). It never fetches. Quote its figures; do not recompute
        them, and keep its sample-size wording ("early signal") in your answer."""
        sources: list[present.Source] = []
        call_args: dict[str, Any] = {}
        for qid in query_ids:
            payload = _payload_for(state, qid)
            if payload is None:
                return ToolResult(success=False, retryable=True, error_code="INSUFFICIENT_EVIDENCE",
                                  summary=f"No fetched result has query_id {qid!r}. Fetched this turn: {sorted(state.by_query) or 'nothing yet'}.")
            sources += present.sources_of(payload)
            call_args = call_args or present.call_args_for(state, qid)
        roles = entity_audit.pick_roles([m for src in sources for m in src.measures])
        # every entity with spend is audited: the spend view leads the join, so an entity another view (orders) has no row for
        # stays in (live 2026-10-19: the 13-row orders view led and 107 of 120 campaigns, the waste, were dropped)
        frame = present.frame_of(sources, roles.spend, present.named_entities(call_args)) if sources and roles else None
        entity_col = next((k for k in (frame.keys if frame else []) if k != (frame.time_col if frame else None)), None)
        if frame is None or roles is None or entity_col is None:
            return ToolResult(success=False, retryable=True, error_code="INSUFFICIENT_EVIDENCE",
                              summary="Nothing to audit: fetch ad spend (with orders and revenue) broken down by the entity "
                                      "(campaign_name, product_title, ...) with metrics_query, then pass its query_id.")
        meta = present.Meta.of(ctx.deps, frame.measures)
        found = entity_audit.audit(entity_audit.entities_of(frame.rows, entity_col, roles), labels=meta.labels, roles=roles,
                                   symbol=present._CURRENCY_SYMBOLS.get((frame.currency or "INR").upper(), "₹"))
        return ToolResult(success=True, summary=found.text())

    async def business_snapshot(ctx: RunContext[SelericDeps]) -> dict[str, Any]:
        """The latest hourly business-health snapshot (one day against the day before, plus 7-day averages and
        anomaly flags), ending at the last complete day. Use for broad "how are we doing" questions with no period named; for
        today, right now or any other period query the metrics."""
        if snapshot_loader is None:
            return {"available": False, "reason": "no snapshot store is configured"}
        try:
            data = await snapshot_loader()
        except Exception:
            _log.warning("business_snapshot_failed", exc_info=True)
            data = None
        if not data:
            return {"available": False, "reason": "the snapshot is missing or stale; query the metrics instead"}
        state.payloads.append(data)
        return data

    async def escalate(ctx: RunContext[SelericDeps], reason: str) -> str:
        """Ask for deeper reasoning for the rest of this turn. Call once, together with your first data calls, when the question needs real analysis (causes, trade-offs, many moving parts) rather than a lookup. ``reason`` is one sentence."""
        set_loop_effort(LOOP_ESCALATED_EFFORT)
        return json.dumps({"ok": True, "note": f"Reasoning effort raised to {LOOP_ESCALATED_EFFORT} for the rest of this turn."})

    helpers = [
        diagnosis.diagnose_metric_change,
        composition.break_down_metric,
        forecasting.forecast_metrics,
        exploration.explore_data,
    ]
    own = [run_python, show_chart, show_table, audit_entities, business_snapshot, escalate]
    mail = build_email_tools(state, is_cancelled=is_cancelled or (lambda: False), figures=figures or (lambda: list(state.payloads)))
    return [*(_tracked(state, fn, raises_effort=fn is diagnosis.diagnose_metric_change) for fn in [*helpers, *own]),
            *(_tracked(state, fn, quotable=False) for fn in mail)]
