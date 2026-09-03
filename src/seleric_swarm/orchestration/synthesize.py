from __future__ import annotations

import json

from seleric_swarm.llm.errors import LLMError
from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMRequestMetadata
from seleric_swarm.runtime import SwarmRuntime
from seleric_swarm.services.numeric_audit import unaudited_numbers


async def synthesize_response(runtime: SwarmRuntime, state: dict) -> dict:
    claims = [c for c in (state.get("claims") or []) if c.get("gate_status") == "passed"]
    evidence = state.get("evidence") or []
    if not claims:
        table = _table_fallback(evidence, claims)
        return {
            "final_response": table,
            "synthesis_fallback": True,
            "llm_calls": 0,
        }

    spec = runtime.prompts.load("synthesizer.response")
    user = spec.render_user(
        {
            "query": state.get("user_query") or "",
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
        leaked = unaudited_numbers(prose, evidence, extra_allowed)
        if leaked:
            return {
                "final_response": _table_fallback(evidence, claims),
                "synthesis_fallback": True,
                "limitations": list(state.get("limitations") or [])
                + [f"Synthesizer output dropped; unaudited numbers {leaked}"],
                "llm_calls": 1,
            }
        return {"final_response": prose, "synthesis_fallback": False, "llm_calls": 1}
    except LLMError as exc:
        return {
            "final_response": _table_fallback(evidence, claims),
            "synthesis_fallback": True,
            "limitations": list(state.get("limitations") or []) + [f"Synthesis LLM failed: {exc.message}"],
            "llm_calls": 1,
        }


def _table_fallback(evidence: list[dict], claims: list[dict]) -> str:
    if claims:
        return " ".join(c.get("text", "") for c in claims)
    if evidence:
        parts = []
        for row in evidence:
            parts.append(f"{row.get('metric_or_fact')}={row.get('value')} ({row.get('evidence_id')})")
        return "Validated evidence: " + "; ".join(parts)
    return "No validated claims are available."
