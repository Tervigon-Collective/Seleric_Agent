"""The answer-draft call with its failure policy (A4): one revision on structural failure,
then ship ``partial`` with the problems stated. The model call is injected (``AnswerLLM``).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from seleric_swarm.answer._assemble import ReportSkeleton
from seleric_swarm.answer._evidence import Evidence
from seleric_swarm.answer._render import render
from seleric_swarm.answer._verify import OFF_SCOPE_CODES, Verdict, verify
from seleric_swarm.contracts.pipeline import AnswerDocument, AnswerDraft, Paragraph, QuestionSpec
from seleric_swarm.semantic import SemanticReadModel

_log = logging.getLogger("seleric.answer")

INSTRUCTIONS = (
    "Write the answer as an AnswerDraft. Never write a number: every figure is a claim whose "
    "ref points at a result set row or a derivation from the evidence list, and paragraphs "
    "cite it as {claim_id}. Use role 'change' only for delta/pct_change derivations, 'share' "
    "only for share derivations, 'total' for a sum or an unsliced row. Use a table block for "
    "a result set with many rows. If evidence is missing or failed, set status 'partial' and "
    "say what is missing in limitations. next_step is one short offer of an investigation the "
    "connected catalogue can already answer — never ask the user to upload data Core can query. "
    "When a report skeleton is provided, write narrative paragraphs and choose which insights "
    "to feature; do not invent chart points, KPI values, or internal step ids (q1, d1, s1.q2). "
    "Name the row (entity) and the measure each figure belongs to in the same sentence — never a "
    "bare list of placeholders, and never describe a figure by its audience instead of its subject. Write plain business language: never quote field names, keys "
    "or ids from the evidence listing, and never name a currency or unit yourself (a figure "
    "prints with its own). limitations hold only what limits this answer — evidence that is "
    "missing, failed or cut to its top rows — not things the question did not ask for."
)

_UNAVAILABLE = AnswerDraft(
    status="failed",
    claims=(),
    blocks=(Paragraph(kind="paragraph", text="I could not write the answer just now. Please ask again."),),
    limitations=("answer_unavailable",),
)


_MAX_ROWS = 20
_MAX_ROWS_TOTAL = 120


def _short(value: float | None) -> float | None:
    """Four significant figures: enough to choose what to say, cheaper than full floats."""
    return None if value is None else float(f"{value:.4g}")


class AnswerLLM(Protocol):
    async def __call__(self, *, instructions: str, prompt: str) -> tuple[AnswerDraft, dict[str, Any]]: ...


def evidence_prompt(
    question: str,
    spec: QuestionSpec | None,
    evidence: Evidence,
    read_model: SemanticReadModel,
    *,
    skeleton: ReportSkeleton | None = None,
) -> str:
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
        for key in ("subject", "verdict", "baseline", "event", "share_of_change", "detail",
                    "largest_leakage", "largest_leakage_stage", "criterion"):
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
    if skeleton is not None:
        body["report_skeleton"] = {
            "status": skeleton.status,
            "block_kinds": [getattr(b, "kind", type(b).__name__) for b in skeleton.blocks],
            "insights": [
                {
                    "id": i.insight_id,
                    "movement": i.movement,
                    "kind": i.explanation_kind,
                    "next_check": i.next_check,
                }
                for i in skeleton.insights[:8]
            ],
            "limitations": skeleton.limitations[:8],
            "note": "Server already built KPI/chart/table blocks from certified evidence. "
                    "Return narrative paragraphs + claims only; do not invent numbers.",
        }
    return json.dumps(body, default=str, separators=(",", ":"))


def _merge_skeleton(draft: AnswerDraft, skeleton: ReportSkeleton | None) -> AnswerDraft:
    """Prepend server-built report blocks; keep the model's narrative paragraphs/claims."""
    if skeleton is None or not skeleton.blocks:
        return draft
    narrative = tuple(b for b in draft.blocks if isinstance(b, Paragraph))
    # Prefer skeleton status when it is stricter than the model's.
    status = draft.status
    if skeleton.status == "partial" and status == "completed":
        status = "partial"
    if skeleton.status == "failed" and status != "failed" and not narrative and not draft.claims:
        status = "failed"
    limitations = tuple(dict.fromkeys([*draft.limitations, *skeleton.limitations]))
    # Narrative leads for simple answers (lookup regression); structured
    # blocks follow so charts/KPI grids still render after the prose.
    return draft.model_copy(
        update={
            "status": status,
            "blocks": (*narrative, *skeleton.blocks),
            "limitations": limitations,
        }
    )


async def answer(
    llm: AnswerLLM | None,
    question: str,
    *,
    spec: QuestionSpec | None,
    evidence: Evidence,
    read_model: SemanticReadModel,
    instructions: str | None = None,
    skeleton: ReportSkeleton | None = None,
) -> tuple[AnswerDocument, dict[str, Any]]:
    """Draft → verify → (one revision) → merge skeleton → render. Never raises."""
    stats: dict[str, Any] = {"attempts": 0}
    if llm is None:
        return render(_UNAVAILABLE, evidence, read_model), {"status": "skipped"}
    prompt = evidence_prompt(question, spec, evidence, read_model, skeleton=skeleton)
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
        verdict = verify(draft, spec, evidence, skeleton=skeleton, read_model=read_model)
        if verdict.ok:
            break
        prompt = prompt + "\nREVISE. Structural problems: " + json.dumps([[i.code, i.detail] for i in verdict.issues])
    if draft is None:
        # Multi-objective reports may still ship a skeleton-only partial. Single-
        # section callers treat answer_unavailable as failed (mission falls back).
        multi_report = skeleton is not None and len({
            getattr(b, "section_id", "") for b in skeleton.blocks if getattr(b, "section_id", "")
        }) > 1
        if multi_report and skeleton is not None and skeleton.blocks:
            draft = AnswerDraft(
                status="partial",
                claims=(),
                blocks=(),
                limitations=tuple(skeleton.limitations) or ("answer_unavailable",),
                next_step="",
            )
            draft = _merge_skeleton(draft, skeleton)
            stats["status"] = draft.status
            return render(draft, evidence, read_model), stats
        stats["status"] = "failed"
        return render(_UNAVAILABLE, evidence, read_model), stats
    stats["issues"] = verdict.codes()
    if OFF_SCOPE_CODES & set(verdict.codes()):
        # Still about another entity after the revision: not an answer to this question.
        stats["status"] = "failed"
        return render(_UNAVAILABLE, evidence, read_model), stats
    updates: dict[str, object] = {}
    if verdict.ok and draft.status == "partial" and skeleton is not None and skeleton.status == "completed":
        # Coverage already knows whether evidence is missing or failed; the model's "partial"
        # (it saw a row cut-off in its listing) must not downgrade a whole answer.
        updates["status"] = "completed"
    if not verdict.ok and draft.status == "completed":
        updates["status"] = "partial"
        updates["limitations"] = (
            *draft.limitations,
            *(f"unverified:{c}" for c in dict.fromkeys(verdict.codes())),
        )
    if {"next_step_asks_for_data", "next_step_off_entity"} & set(verdict.codes()):
        updates["next_step"] = ""
    if updates:
        draft = draft.model_copy(update=updates)
    draft = _merge_skeleton(draft, skeleton)
    stats["status"] = draft.status
    return render(draft, evidence, read_model), stats
