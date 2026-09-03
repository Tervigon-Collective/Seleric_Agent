"""Optional LLM-as-judge for synthesis faithfulness.

Never invoked on the live request path and never in CI (the eval CLI only runs it
behind ``--judge``, which also requires ``--live-llm``). A judge must not score
money, counts, dates, routing, or schema — those stay on deterministic evaluators.
It only checks that the prose stayed faithful to the already-gated claims.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from seleric_swarm.llm.port import ChatMessage, LLMPort, LLMRequest, LLMRequestMetadata

JUDGE_RUBRIC = (
    "You are a strict evaluator. You are given a user question, a set of GATED CLAIMS "
    "(already verified numbers), and an ANSWER written for the user.\n"
    "Judge ONLY faithfulness:\n"
    "  faithful = 1 if every factual statement in the ANSWER is supported by the GATED "
    "CLAIMS, else 0.\n"
    "  extra_numbers = 1 if the ANSWER contains any number that is not present in the "
    "GATED CLAIMS, else 0.\n"
    "Do NOT recompute metrics. Do NOT judge whether a number is 'correct' in the world. "
    "Do NOT reward or penalise style.\n"
    'Reply with ONLY a JSON object: {"faithful": 0|1, "extra_numbers": 0|1, "rationale": "<=200 chars"}'
)


class JudgeVerdict(BaseModel):
    faithful: int = 0
    extra_numbers: int = 1
    rationale: str = ""
    parsed: bool = True

    @property
    def passed(self) -> bool:
        return self.faithful == 1 and self.extra_numbers == 0


def _parse(text: str) -> JudgeVerdict:
    raw = text.strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(raw[start : end + 1])
            return JudgeVerdict(
                faithful=int(bool(obj.get("faithful"))),
                extra_numbers=int(bool(obj.get("extra_numbers", 1))),
                rationale=str(obj.get("rationale", ""))[:200],
            )
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    return JudgeVerdict(faithful=0, extra_numbers=1, rationale=f"unparseable judge reply: {raw[:80]}", parsed=False)


async def judge_synthesis(
    llm: LLMPort,
    *,
    query: str,
    answer: str,
    claims: list[dict[str, Any]],
    judge_model: str,
) -> JudgeVerdict:
    """Score one synthesis. Returns a failing verdict on any LLM/parse error."""
    user = (
        f"QUESTION:\n{query}\n\n"
        f"GATED_CLAIMS:\n{json.dumps(claims, default=str, indent=2)}\n\n"
        f"ANSWER:\n{answer}\n"
    )
    request = LLMRequest(
        messages=[
            ChatMessage(role="system", content=JUDGE_RUBRIC),
            ChatMessage(role="user", content=user),
        ],
        model=judge_model,
        temperature=0,
        max_tokens=200,
        metadata=LLMRequestMetadata(
            agent_id="synthesis_judge",
            agent_version="0.1.0",
            prompt_id="eval.synthesis_judge",
            prompt_version="1",
            workflow_name="eval_llm_judge",
            workflow_version="1.0.0",
            model=judge_model,
        ),
        tags=["eval", "llm_judge", "synthesis_faithfulness"],
    )
    try:
        resp = await llm.complete(request)
    except Exception as exc:  # noqa: BLE001 - a broken judge must fail closed, not crash the suite
        return JudgeVerdict(faithful=0, extra_numbers=1, rationale=f"judge call failed: {exc}", parsed=False)
    return _parse(resp.text)
