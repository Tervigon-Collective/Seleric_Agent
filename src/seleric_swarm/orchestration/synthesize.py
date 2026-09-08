from __future__ import annotations

import json
import re as _re

from seleric_swarm.llm.errors import LLMError
from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMRequestMetadata
from seleric_swarm.runtime import SwarmRuntime
from seleric_swarm.services.numeric_audit import unaudited_numbers

_ID_STRIP_RE = _re.compile(r"\s*[\[(]?(CL|EV|M|T)-[0-9a-f]+[\])]?", _re.IGNORECASE)


async def synthesize_response(runtime: SwarmRuntime, state: dict) -> dict:
    claims = [c for c in (state.get("claims") or []) if c.get("gate_status") == "passed"]
    evidence = state.get("evidence") or []
    user_query: str = state.get("user_query") or ""
    primary_metric: str | None = state.get("metric_id")
    if not claims:
        table = _table_fallback(evidence, claims, primary_metric=primary_metric)
        return {
            "final_response": table,
            "synthesis_fallback": True,
            "llm_calls": 0,
        }

    spec = runtime.prompts.load("synthesizer.response")
    user = spec.render_user(
        {
            "query": user_query,
            "gated_claims_json": json.dumps(claims, default=str),
            "evidence_json": json.dumps(evidence, default=str),
        }
    )
    request = LLMRequest(
        messages=[
            ChatMessage(role="system", content=spec.system),
            ChatMessage(role="user", content=user),
        ],
        model=spec.model,
        temperature=spec.temperature,
        max_tokens=spec.max_tokens,
        timeout_s=runtime.settings.llm_timeout_s,
        metadata=LLMRequestMetadata(
            request_id=state.get("request_id"),
            session_id=state.get("session_id"),
            mission_id=state.get("mission_id"),
            task_id=state.get("task_id"),
            agent_id="coordinator_agent",
            agent_version="0.1.0",
            prompt_id=spec.id,
            prompt_version=spec.version,
            workflow_name=runtime.settings.workflow_name,
            workflow_version=runtime.settings.workflow_version,
            query_class=state.get("query_class"),
        ),
        tags=["synthesizer", spec.id],
    )
    try:
        raw = await runtime.llm.complete(request)
        prose = raw.text.strip()
        extra_allowed = []
        for row in evidence:
            extra_allowed.append(row.get("value"))
            tr = row.get("time_range") or {}
            extra_allowed.extend([tr.get("start"), tr.get("end")])
        # Pass the original user query so query parameters (e.g. "5" in
        # "last 5 months") are not flagged as fabricated metric values.
        leaked = unaudited_numbers(prose, evidence, extra_allowed, query_text=user_query)
        if leaked:
            return {
                "final_response": _table_fallback(evidence, claims, primary_metric=primary_metric),
                "synthesis_fallback": True,
                "limitations": list(state.get("limitations") or [])
                + [f"Synthesizer output dropped; unaudited numbers {leaked}"],
                "llm_calls": 1,
            }
        return {"final_response": prose, "synthesis_fallback": False, "llm_calls": 1}
    except LLMError as exc:
        return {
            "final_response": _table_fallback(evidence, claims, primary_metric=primary_metric),
            "synthesis_fallback": True,
            "limitations": list(state.get("limitations") or []) + [f"Synthesis LLM failed: {exc.message}"],
            "llm_calls": 1,
        }


def _dim_label(row: dict) -> str:
    """Extract a human-readable label from the first dimension value of a row."""
    dims = row.get("dimensions") or {}
    if not dims:
        return str(row.get("metric_or_fact") or "")
    return str(next(iter(dims.values())))


def _table_fallback(
    evidence: list[dict],
    claims: list[dict],
    primary_metric: str | None = None,
) -> str:
    """Last-resort formatter when synthesis is dropped or skipped.

    When evidence contains dimension-breakdown rows (ranking results), present
    them as a numbered ranked list sorted by value descending — driven entirely
    by what the evidence rows contain, no metric names hardcoded.  Plain
    aggregate queries fall back to the first most-relevant claim text.
    """
    # Dimension-breakdown path: ranking evidence exists
    ranked = [
        row for row in evidence
        if row.get("dimensions")
        and (primary_metric is None or row.get("metric_or_fact") == primary_metric)
    ]
    if ranked:
        ranked.sort(key=lambda r: -(float(r.get("value") or 0)))
        lines: list[str] = []
        for i, row in enumerate(ranked[:10], start=1):
            label = _dim_label(row)
            value = row.get("value")
            unit = row.get("unit") or ""
            unit_str = f" {unit}" if unit else ""
            lines.append(f"{i}. {label} — {value}{unit_str}")
        return "\n".join(lines)

    # Plain aggregate path: return the first most-relevant claim text only
    if claims:
        # Prefer claim whose text mentions the primary metric, else use first.
        target = claims[0]
        if primary_metric:
            for c in claims:
                if primary_metric in (c.get("text") or ""):
                    target = c
                    break
        text = _ID_STRIP_RE.sub("", target.get("text", "")).strip()
        return text or "No validated claims are available."

    if evidence:
        parts = [f"{row.get('metric_or_fact')}={row.get('value')}" for row in evidence]
        return "Validated evidence: " + "; ".join(parts)
    return "No validated claims are available."
