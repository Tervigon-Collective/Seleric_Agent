"""A chart for the answer's main result, when the model asked for none.

The main result is the fetched query whose rows the answer actually quotes (the most figures matched verbatim), with
enough rows to plot. The form comes from the evidence shape (``analytics/visualization``: a time axis draws a line, a
breakdown draws bars); this module only picks which result and whether a chart is wanted at all.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, cast

from pydantic_ai import RunContext

from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.grounding import is_query_payload, measured_in
from seleric_swarm.agent.mcp_toolset import TurnState
from seleric_swarm.toolsets import analytics

_log = logging.getLogger("seleric.agent.auto_chart")

MIN_ROWS = 3  # two bars (this period, last period) say nothing a sentence does not
# Charts the transcript does not show: the inspector draws these, so they do not count as "the answer has a chart".
_INSPECTOR_CHARTS = frozenset({"causal_graph", "dag", "gcm", "gcm_graph", "causal_dag", "dowhy_graph"})


def _query_id(payload: dict[str, Any]) -> str:
    prov = payload.get("provenance")
    return str(payload.get("query_id") or (prov.get("query_id") if isinstance(prov, dict) else "") or "")


def has_transcript_chart(deps: SelericDeps) -> bool:
    return any(
        a.artifact_type == "chart_spec"
        and isinstance(a.payload, dict)
        and a.payload.get("chart_type") not in _INSPECTOR_CHARTS
        for a in deps.artifact_store.list_for_mission(deps.mission_id)
    )


def main_result(answer: str, state: TurnState) -> tuple[str, list[str], dict[str, Any]] | None:
    """(query_id, evidence ids, payload) of the fetched result the answer leans on most, or None."""
    best: tuple[int, int, str, list[str], dict[str, Any]] | None = None
    candidates: list[dict[str, Any]] = []
    for payload in state.payloads:
        if isinstance(payload, dict) and isinstance(payload.get("parts"), list) and payload["parts"]:
            candidates.extend(p for p in payload["parts"] if isinstance(p, dict))  # a multi-view result: chart one part
        elif isinstance(payload, dict):
            candidates.append(payload)
    for payload in candidates:
        if not is_query_payload(payload):
            continue
        qid = _query_id(payload)
        ids = state.by_query.get(qid) or []
        if len(ids) < MIN_ROWS:
            continue
        score = measured_in(answer, payload)
        if score > 0 and (best is None or (score, len(ids)) > (best[0], best[1])):
            best = (score, len(ids), qid, ids, payload)
    return None if best is None else (best[2], best[3], best[4])


def _title(deps: SelericDeps, payload: dict[str, Any]) -> str:
    prov = payload.get("provenance")
    ids = [m for m in ((prov or {}).get("metric_ids") or []) if isinstance(m, str)] if isinstance(prov, dict) else []
    labels = {m.id: m.label for m in deps.catalogue.metrics if m.label}
    names = [labels.get(m, m.replace("_", " ")) for m in ids[:3]]
    return ", ".join(names) or "Result"


async def auto_chart(deps: SelericDeps, state: TurnState, answer: str) -> str | None:
    """Build the chart artifact for the main result; returns its artifact id, or None when no chart is wanted."""
    if not answer.strip() or has_transcript_chart(deps):
        return None
    picked = main_result(answer, state)
    if picked is None:
        return None
    qid, evidence_ids, payload = picked
    ctx = cast(RunContext[SelericDeps], SimpleNamespace(deps=deps))  # the chart tool reads only ``ctx.deps``
    try:
        result = await analytics.generate_visualization(
            ctx, evidence_ids, "the main result of this answer", _title(deps, payload)
        )
    except Exception:
        _log.warning("auto_chart_failed query_id=%s", qid, exc_info=True)
        return None
    if not result.success or not result.artifact_ids:
        _log.info("auto_chart_skipped query_id=%s reason=%s", qid, result.summary[:160])
        return None
    state.steps.append({"kind": "auto_chart", "query_id": qid, "artifact_id": result.artifact_ids[0], "rows": len(evidence_ids)})
    return result.artifact_ids[0]
