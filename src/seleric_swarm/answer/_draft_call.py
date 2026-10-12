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


_MAX_ROWS = 20
# Cap breakdown rows across all result sets, not just per set: a plan can emit
# twenty result sets and a per-set cap alone lets the listing grow unbounded.
# Single-value result sets are one value each and are never charged against it.
_MAX_ROWS_TOTAL = 120


def _short(value: float | None) -> float | None:
    """Four significant figures: enough to choose what to say, cheaper than full floats."""
    return None if value is None else float(f"{value:.4g}")


class AnswerLLM(Protocol):
    async def __call__(self, *, instructions: str, prompt: str) -> tuple[AnswerDraft, dict[str, Any]]: ...


def evidence_prompt(question: str, spec: QuestionSpec | None, evidence: Evidence, read_model: SemanticReadModel) -> str:
    """Compact JSON listing of what may be cited (ids, metric labels, row keys; values included
    so the model can choose what to say, never so it can type them)."""
    items: list[dict[str, Any]] = []
    row_budget = _MAX_ROWS_TOTAL
    for rs in evidence.result_sets():
        item: dict[str, Any] = {
            "ref": rs.ref.id, "kind": "result_set",
            "metric": read_model.label_for(rs.metric_id) or rs.metric_id,
            "window": f"{rs.window.role} {rs.window.start}..{rs.window.end}",
        }
        if rs.error:
            item["error"] = rs.error.code
        elif rs.breakdowns or len(rs.rows) != 1:
            # The answer cites rows by key; values are shown only so the model can pick what to say.
            ordered = sorted(rs.rows, key=lambda r: -abs(r.value or 0.0))
            take = min(_MAX_ROWS, max(0, row_budget))
            item["rows"] = [[r.dimensions, _short(r.value)] for r in ordered[:take]]
            row_budget -= take
            if len(rs.rows) > take:
                item["rows_not_shown"] = len(rs.rows) - take
        else:
            item["value"] = _short(rs.rows[0].value)
        items.append(item)
    for d in evidence.derivations():
        ditem: dict[str, Any] = {"ref": d.ref.id, "kind": "derivation", "op": d.op, "value": _short(d.value)}
        if d.error:
            ditem["error"] = d.error.code
        for key in ("subject", "verdict", "baseline", "event", "share_of_change", "detail"):
            value = d.params.get(key)
            if value not in (None, ""):
                ditem[key] = _short(value) if isinstance(value, float) else value
        items.append(ditem)
    body: dict[str, Any] = {"question": question, "evidence": items}
    if spec is not None:
        body["asked"] = {
            "shape": spec.shape,
            "measures": [m.metric_id or m.phrase for m in spec.measures],
            "by": list(spec.breakdowns),
            "entities": {e.dimension: list(e.values) for e in spec.entities},
            "periods": [f"{w.role} {w.start}..{w.end}" for w in spec.windows],
        }
    return json.dumps(body, default=str, separators=(",", ":"))


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
