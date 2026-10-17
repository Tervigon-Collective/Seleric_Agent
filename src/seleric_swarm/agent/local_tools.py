"""The loop's local tools: analysis helpers the model calls when a question needs them (never a forced stage).

* Existing toolsets, registered as they are: ``diagnose_metric_change``, ``break_down_metric``, ``forecast_metrics``,
  ``explore_data`` (each fetches its own data and records evidence).
* ``run_python`` and ``show_chart`` take the ``query_id`` of a fetched result instead of evidence ids: the model sees
  query ids in every MCP result, the evidence rows behind them stay server-side.
* ``show_table`` attaches a full table of a fetched result next to the answer (a TABLE part).
* ``business_snapshot`` returns the latest hourly business-health snapshot as data, when it is fresh.
* ``escalate`` raises the reasoning effort for the rest of the turn.
"""

from __future__ import annotations

import functools
import json
import logging
import time
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import RunContext

from seleric_swarm.agent import present
from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.grounding import answer_numbers
from seleric_swarm.agent.mcp_toolset import TurnState
from seleric_swarm.agent.model import LOOP_ESCALATED_EFFORT, LOOP_WRITEUP_EFFORT, set_loop_effort
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.toolsets import (
    analytics,
    composition,
    diagnosis,
    exploration,
    forecasting,
    sandbox,
)

_log = logging.getLogger("seleric.agent.local_tools")


MAX_TABLE_ROWS = 200


def query_id_of(payload: dict[str, Any]) -> str:
    prov = payload.get("provenance")
    return str(payload.get("query_id") or (prov.get("query_id") if isinstance(prov, dict) else "") or "")


def _payload_for(state: TurnState, query_id: str) -> dict[str, Any] | None:
    """The fetched result with this query id: a whole result, or one part of a multi-view result."""
    for p in state.payloads:
        if not isinstance(p, dict):
            continue
        if query_id_of(p) == query_id:
            return p
        for part in p.get("parts") or []:
            if isinstance(part, dict) and query_id_of(part) == query_id:
                return part
    return None


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


def _tracked(state: TurnState, fn: Any, *, raises_effort: bool = False) -> Any:
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
        state.steps.append({"kind": "tool_call", "tool": fn.__name__, "args": {k: str(v)[:200] for k, v in kwargs.items()}})
        step = {"kind": "tool_return", "tool": fn.__name__, "ok": not failed, "ms": ms, "result": str(summary)[:2000]}
        source = getattr(getattr(result, "provenance", None), "source_metadata", None)
        if isinstance(source, dict) and isinstance(source.get("phase_ms"), dict):
            step["phase_ms"] = source["phase_ms"]
        state.steps.append(step)
        if failed:  # MCP failures are recorded by the toolset; a helper that returns ok:false must show up the same way
            state.errors.append(f"{fn.__name__}: {str(summary)[:300]}")
        _remember_figures(state, result)
        if raises_effort and not failed:  # the engine did the causal reasoning; a refusal leaves real reasoning to do
            set_loop_effort(LOOP_WRITEUP_EFFORT)
        return _view(result)

    wrapper.__name__ = wrapper.__qualname__ = fn.__name__
    return wrapper


def build_local_tools(state: TurnState, *, snapshot_loader: Any = None) -> list[Any]:
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
            period_start, period_end, query_id and ``dimensions`` (a dict); every dimension is also a key of the row.
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
        return await analytics.generate_visualization(
            ctx, _evidence_for(state, query_ids) or [], intent, title, chart_type
        )

    async def show_table(ctx: RunContext[SelericDeps], query_id: str, caption: str = "") -> ToolResult:
        """Show the full rows of one fetched result as a sortable table next to the answer. Use it for a long breakdown
        (many campaigns, products, days) instead of pasting every row into the text; keep the text to the finding and
        the top rows. A result with several views is shown as one table joined on the shared entity. ``query_id`` is
        the result to show; ``caption`` says what the table is."""
        payload = _payload_for(state, query_id)
        call_args = present.call_args_for(state, query_id)
        frame = present.frame_of(present.sources_of(payload), present.sort_measure(call_args))
        if frame is None or not frame.rows:
            return ToolResult(success=False, summary=f"No fetched result with rows has query_id {query_id!r}.",
                              error_code="INSUFFICIENT_EVIDENCE", retryable=False)
        present.apply_call_hints(frame, call_args)  # the query's measure order and ranking
        content = present.table_content(ctx.deps, frame, query_id=query_id, caption=caption, limit=MAX_TABLE_ROWS)
        state.parts.append({"type": "TABLE", "content": content})
        more = f" (first {len(content['rows'])} of {content['total_rows']} rows)" if content["total_rows"] > len(content["rows"]) else ""
        return ToolResult(success=True, summary=f"Table of {len(content['rows'])} rows attached to your answer{more}. "
                          "Do not repeat the full table in the text.")

    async def business_snapshot(ctx: RunContext[SelericDeps]) -> dict[str, Any]:
        """The latest hourly business-health snapshot (one day against the day before, plus 7-day averages and
        anomaly flags). Use for broad "how are we doing" questions; query metrics for any other period."""
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
    own = [run_python, show_chart, show_table, business_snapshot, escalate]
    return [*(_tracked(state, fn, raises_effort=fn is diagnosis.diagnose_metric_change) for fn in [*helpers, *own])]
