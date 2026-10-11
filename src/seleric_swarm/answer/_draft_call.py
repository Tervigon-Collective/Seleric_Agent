"""The answer-draft call with its failure policy (A4): one revision on structural failure,
then ship ``partial`` with the problems stated. The model call is injected (``AnswerLLM``).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from seleric_swarm.answer._evidence import Evidence
from seleric_swarm.answer._render import render
from seleric_swarm.answer._verify import Verdict, verify
from seleric_swarm.contracts.pipeline import AnswerDocument, AnswerDraft, Paragraph, QuestionSpec
from seleric_swarm.semantic import SemanticReadModel

_log = logging.getLogger("seleric.answer")

INSTRUCTIONS = (
    "Write the answer as an AnswerDraft. Never write a number: every figure is a claim whose "
    "ref points at a result set row or a derivation from the evidence list, and paragraphs "
    "cite it as {claim_id}. Use role 'change' only for delta/pct_change derivations, 'share' "
    "only for share derivations, 'total' for a sum or an unsliced row. Use a table block for "
    "a result set with many rows. If evidence is missing or failed, set status 'partial' and "
    "say what is missing in limitations. next_step is one short offer."
)

_UNAVAILABLE = AnswerDraft(
    status="failed",
    claims=(),
    blocks=(Paragraph(kind="paragraph", text="I could not write the answer just now. Please ask again."),),
    limitations=("answer_unavailable",),
)


class AnswerLLM(Protocol):
    async def __call__(self, *, instructions: str, prompt: str) -> tuple[AnswerDraft, dict[str, Any]]: ...


def evidence_prompt(question: str, spec: QuestionSpec | None, evidence: Evidence, read_model: SemanticReadModel) -> str:
    """Compact JSON listing of what may be cited (ids, metric labels, row keys; values included
    so the model can choose what to say, never so it can type them)."""
    items: list[dict[str, Any]] = []
    for rs in evidence.result_sets():
        items.append({
            "ref": {"kind": "result_set", "id": rs.ref.id},
            "metric": rs.metric_id, "label": read_model.label_for(rs.metric_id) or rs.metric_id,
            "window": rs.window.role, "breakdowns": list(rs.breakdowns),
            "rows": [{"key": r.dimensions, "value": r.value} for r in rs.rows[:50]],
            "error": rs.error.code if rs.error else None,
        })
    for d in evidence.derivations():
        items.append({
            "ref": {"kind": "derivation", "id": d.ref.id}, "op": d.op,
            "inputs": [r.id for r in d.inputs], "value": d.value,
            "error": d.error.code if d.error else None,
        })
    body: dict[str, Any] = {"question": question, "evidence": items}
    if spec is not None:
        body["spec"] = spec.model_dump(mode="json", include={"kind", "shape", "measures", "breakdowns", "entities", "windows"})
    return json.dumps(body, default=str)


async def answer(
    llm: AnswerLLM | None,
    question: str,
    *,
    spec: QuestionSpec | None,
    evidence: Evidence,
    read_model: SemanticReadModel,
    instructions: str | None = None,
) -> tuple[AnswerDocument, dict[str, Any]]:
    """Draft → verify → (one revision) → render. Never raises."""
    stats: dict[str, Any] = {"attempts": 0}
    if llm is None:
        return render(_UNAVAILABLE, evidence, read_model), {"status": "skipped"}
    prompt = evidence_prompt(question, spec, evidence, read_model)
    draft: AnswerDraft | None = None
    verdict = Verdict()
    for attempt in (1, 2):
        stats["attempts"] = attempt
        try:
            draft, usage = await llm(instructions=instructions or INSTRUCTIONS, prompt=prompt)
            stats.update(usage)
        except Exception as exc:
            _log.warning("answer_draft_failed attempt=%d", attempt, exc_info=True)
            stats["error"] = type(exc).__name__
            continue
        verdict = verify(draft, spec, evidence)
        if verdict.ok:
            break
        prompt = prompt + "\nREVISE. Structural problems: " + json.dumps([[i.code, i.detail] for i in verdict.issues])
    if draft is None:
        stats["status"] = "failed"
        return render(_UNAVAILABLE, evidence, read_model), stats
    stats["issues"] = verdict.codes()
    if not verdict.ok and draft.status == "completed":
        draft = draft.model_copy(update={
            "status": "partial",
            "limitations": (*draft.limitations, *(f"unverified:{c}" for c in dict.fromkeys(verdict.codes()))),
        })
    stats["status"] = draft.status
    return render(draft, evidence, read_model), stats
