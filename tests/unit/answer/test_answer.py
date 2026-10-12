"""Phase 3: render, verify, draft loop and turn summary (invented catalogue)."""

from __future__ import annotations

import json
from datetime import date

from seleric_swarm.answer import Evidence, answer, render, turn_summary, verify
from seleric_swarm.answer._draft_call import _MAX_ROWS_TOTAL, evidence_prompt
from seleric_swarm.contracts.pipeline import (
    AnswerDraft,
    Claim,
    Derivation,
    EntitySpec,
    FilterSpec,
    MeasureSpec,
    Paragraph,
    QuestionSpec,
    RankingSpec,
    Ref,
    ResultRow,
    ResultSet,
    StageError,
    TableBlock,
    WindowSpec,
)
from seleric_swarm.semantic import SemanticReadModel

MODEL = SemanticReadModel.from_payload({
    "catalogue_version": "zz-v1",
    "metrics": [{"id": "zz_take", "label": "Take", "unit": "INR", "aggregation": "additive",
                 "supported_dimensions": ["zz_zone"]}],
    "dimensions": [{"id": "zz_zone"}],
})
EV = WindowSpec(role="event", start=date(2026, 10, 9), end=date(2026, 10, 9))
BASE = WindowSpec(role="baseline", start=date(2026, 10, 8), end=date(2026, 10, 8))
NOT_X = FilterSpec(dimension="zz_zone", operator="not_in", values=("x",))


def rs(rid: str, window: WindowSpec, rows: list[tuple[dict[str, str], float]], *, breakdowns=(), filters=(), error=None) -> ResultSet:
    return ResultSet(
        ref=Ref(kind="result_set", id=rid), step_id=rid, metric_id="zz_take", unit="INR", window=window,
        breakdowns=tuple(breakdowns), filters=tuple(filters), error=error,
        rows=tuple(ResultRow(evidence_id=f"{rid}-{i}", dimensions=d, value=v) for i, (d, v) in enumerate(rows)),
    )


Q1 = rs("q1", EV, [({}, 1500.0)])
B1 = rs("b1", BASE, [({}, 1200.0)])
D1 = rs("d1", EV, [({"zz_zone": "north"}, 900.0), ({"zz_zone": "south"}, 600.0)], breakdowns=("zz_zone",))
PCT = Derivation(ref=Ref(kind="derivation", id="pct"), op="pct_change",
                 inputs=(Ref(kind="result_set", id="b1"), Ref(kind="result_set", id="q1")),
                 params={}, value=0.25, unit=None)
EVIDENCE = Evidence([Q1, B1, D1, PCT])
SPEC = QuestionSpec(kind="analysis", shape="period_comparison",
                    measures=(MeasureSpec(phrase="take", metric_id="zz_take"),), filters=(), entities=(),
                    breakdowns=("zz_zone",), windows=(EV, BASE), grain=None, ranking=None, catalogue_version="zz-v1")


def claim(cid: str, ref: Ref, role="headline", key=None, label="Take") -> Claim:
    return Claim(claim_id=cid, ref=ref, row_key=key or {}, label=label, role=role)


def para(text: str) -> Paragraph:
    return Paragraph(kind="paragraph", text=text)


GOOD = AnswerDraft(
    status="completed",
    claims=(claim("now", Q1.ref, "total"), claim("chg", PCT.ref, "change"),
            claim("n", D1.ref, "support", {"zz_zone": "north"}), claim("was", B1.ref, "context")),
    blocks=(para("Take was {now} ({chg} vs {was}); north gave {n}."),
            TableBlock(kind="table", result_set=D1.ref, columns=("zz_zone", "value"))),
    next_step="Want it by week?",
)


def test_render_fills_numbers_and_tables() -> None:
    doc = render(GOOD, EVIDENCE, MODEL)
    assert "Take was INR 1,500 (+25.0% vs INR 1,200); north gave INR 900." in doc.rendered_markdown
    assert "| zz_zone | Take |" in doc.rendered_markdown and "| south | INR 600 |" in doc.rendered_markdown
    assert {"q1-0", "b1-0", "d1-0", "d1-1"} <= set(doc.evidence_ids)


def test_good_draft_verifies() -> None:
    assert verify(GOOD, SPEC, EVIDENCE).codes() == []


def test_digits_and_unknown_placeholders() -> None:
    bad = GOOD.model_copy(update={"blocks": (para("Take was 1500 and {ghost}."), *GOOD.blocks[1:])})
    assert {"digits_outside_placeholders", "unknown_placeholder"} <= set(verify(bad, SPEC, EVIDENCE).codes())


def test_unresolved_and_role_mismatch() -> None:
    bad = GOOD.model_copy(update={"claims": (claim("now", Q1.ref, "change"), claim("x", D1.ref),
                                             claim("t", D1.ref, "total", {"zz_zone": "north"}), *GOOD.claims[1:])})
    codes = verify(bad, SPEC, EVIDENCE).codes()
    assert "unresolved_claim" in codes and codes.count("role_mismatch") >= 2


def test_coverage_window_breakdown_measure_entity() -> None:
    spec = SPEC.model_copy(update={"entities": (EntitySpec(dimension="zz_zone", values=("east",)),)})
    only_now = AnswerDraft(status="completed", claims=(claim("now", Q1.ref),), blocks=(para("{now}"),))
    codes = set(verify(only_now, spec, EVIDENCE).codes())
    assert {"window_not_covered", "breakdown_not_covered", "entity_not_covered"} <= codes


def test_exclusion_must_be_carried() -> None:
    spec = SPEC.model_copy(update={"filters": (NOT_X,)})
    assert "exclusion_dropped" in verify(GOOD, spec, EVIDENCE).codes()


def test_status_consistency() -> None:
    failed = rs("q9", EV, [], error=StageError(stage="execute", code="boom", message="x"))
    ev = Evidence([Q1, B1, D1, PCT, failed])
    draft = GOOD.model_copy(update={"blocks": (*GOOD.blocks, TableBlock(kind="table", result_set=failed.ref, columns=()))})
    assert "completed_with_failed_evidence" in verify(draft, SPEC, ev).codes()
    assert "partial_without_limitation" in verify(GOOD.model_copy(update={"status": "partial"}), SPEC, EVIDENCE).codes()


def _llm(*drafts: AnswerDraft):
    queue = list(drafts)
    prompts: list[str] = []

    async def call(*, instructions: str, prompt: str):
        prompts.append(prompt)
        return queue.pop(0), {"model": "scripted"}
    call.prompts = prompts  # type: ignore[attr-defined]
    return call


async def test_one_revision_then_ok() -> None:
    bad = GOOD.model_copy(update={"blocks": (para("Take was 1500."),)})
    llm = _llm(bad, GOOD)
    _doc, stats = await answer(llm, "take?", spec=SPEC, evidence=EVIDENCE, read_model=MODEL)
    assert stats["attempts"] == 2 and stats["status"] == "completed" and "REVISE" in llm.prompts[1]


async def test_still_bad_ships_partial() -> None:
    bad = GOOD.model_copy(update={"blocks": (para("Take was 1500."),)})
    doc, _stats = await answer(_llm(bad, bad), "take?", spec=SPEC, evidence=EVIDENCE, read_model=MODEL)
    assert doc.draft.status == "partial" and any(x.startswith("unverified:") for x in doc.draft.limitations)


async def test_no_llm_is_failed_never_raises() -> None:
    doc, _ = await answer(None, "take?", spec=SPEC, evidence=EVIDENCE, read_model=MODEL)
    assert doc.draft.status == "failed"


def test_turn_summary_is_typed() -> None:
    spec = SPEC.model_copy(update={"ranking": RankingSpec(by_metric_id="zz_take", order="desc", limit=2)})
    t = turn_summary(spec, GOOD, EVIDENCE, MODEL)
    assert t.offer == "Want it by week?" and t.metric_labels == ("Take",)
    assert t.entities == {"zz_zone": ("north",)} and t.top_items == ("north", "south")
    assert t.windows == (EV, BASE)


def test_investigation_answer_covers_through_findings() -> None:
    premise = Derivation(ref=Ref(kind="derivation", id="e1.f0"), op="finding:premise", inputs=(),
                         params={"metric_id": "zz_take", "subject": "zz_take"}, value=-90.0, unit=None)
    seg = Derivation(ref=Ref(kind="derivation", id="e1.f1"), op="finding:segment", inputs=(),
                     params={"subject": "zz_zone=north"}, value=-90.0, unit=None)
    draft = AnswerDraft(status="completed", claims=(claim("p", premise.ref, "support"), claim("s", seg.ref, "support")),
                        blocks=(para("Take moved {p}; most of it was north ({s})."),))
    assert verify(draft, SPEC, Evidence([premise, seg])).ok


def test_evidence_prompt_bounds_breakdown_rows_across_result_sets() -> None:
    """Many multi-row result sets stay within the global row budget, not 20 each."""
    sets = [
        rs(f"b{i}", EV, [({"zz_zone": f"z{j}"}, float(j + 1)) for j in range(30)], breakdowns=("zz_zone",))
        for i in range(10)
    ]
    body = json.loads(evidence_prompt("how did take move by zone?", None, Evidence(sets), MODEL))
    total_rows = sum(len(item.get("rows", [])) for item in body["evidence"])
    assert total_rows <= _MAX_ROWS_TOTAL
    assert any("rows_not_shown" in item for item in body["evidence"])


def test_evidence_prompt_keeps_single_value_rows() -> None:
    """Single-value result sets are not charged against the breakdown row budget."""
    sets = [rs(f"v{i}", EV, [({}, float(i + 1))]) for i in range(25)]
    body = json.loads(evidence_prompt("take?", None, Evidence(sets), MODEL))
    assert len(body["evidence"]) == 25
    assert all("value" in item for item in body["evidence"])
