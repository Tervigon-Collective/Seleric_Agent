"""v2 stages in shadow: understand → spec → plan beside the legacy decision (invented catalogue)."""

from __future__ import annotations

import asyncio
from datetime import date

from seleric_swarm.contracts.pipeline import MeasurePhrase, QuestionDraft, WindowExpr, WindowSlot
from seleric_swarm.pipeline import (
    LegacyDecision,
    ShadowInputs,
    ShadowModes,
    collect_shadow,
    start_shadow,
)
from seleric_swarm.semantic import SemanticReadModel

AS_OF = date(2026, 10, 10)
MODEL = SemanticReadModel.from_payload({
    "catalogue_version": "zz-v1",
    "metrics": [{"id": "zz_take", "label": "Take", "aggregation": "additive", "supported_dimensions": ["zz_zone"]}],
    "dimensions": [{"id": "zz_zone"}],
})
DRAFT = QuestionDraft(
    kind="analysis", shape="lookup",
    measures=(MeasurePhrase(words="take", metric_id="zz_take"),),
    windows=(WindowSlot(role="event", expr=WindowExpr(name="yesterday"), source_span="yesterday"),),
    names_period=True,
)
YESTERDAY = (date(2026, 10, 9), date(2026, 10, 9))
BOTH = ShadowModes(understand="shadow", planner="shadow")


def _llm(output=DRAFT, *, fail: bool = False, delay: float = 0.0):
    async def call(*, instructions: str, prompt: str):
        del instructions, prompt
        if delay:
            await asyncio.sleep(delay)
        if fail:
            raise RuntimeError("down")
        return output, {"input_tokens": 1, "output_tokens": 1, "model": "scripted"}
    return call


def _inputs() -> ShadowInputs:
    return ShadowInputs(question="take yesterday", as_of=AS_OF, read_model=MODEL)


async def test_off_starts_nothing() -> None:
    assert start_shadow(_llm(), _inputs(), modes=ShadowModes(), mission_id="m") is None
    assert await collect_shadow(None, LegacyDecision()) is None


async def test_agreement_is_recorded_with_the_v2_plan() -> None:
    task = start_shadow(_llm(), _inputs(), modes=BOTH, mission_id="m")
    record = await collect_shadow(task, LegacyDecision(kind="analysis", shape="lookup",
                                                       metric_ids=("zz_take",), windows=(YESTERDAY,)))
    assert record is not None and record["status"] == "ok"
    assert record["plan"]["template_id"] == "lookup" and record["plan"]["metrics"] == ["zz_take"]
    assert record["windows"] == [["2026-10-09", "2026-10-09"]]
    assert record["disagree"] == []


async def test_disagreements_are_named_per_field() -> None:
    task = start_shadow(_llm(), _inputs(), modes=BOTH, mission_id="m")
    record = await collect_shadow(task, LegacyDecision(
        kind="analysis", shape="trend", metric_ids=("zz_other",),
        windows=((date(2026, 10, 3), date(2026, 10, 9)),), filter_values=("north",)))
    assert record is not None
    assert set(record["disagree"]) == {"shape", "metrics", "windows", "filters"}


async def test_understand_only_mode_skips_the_planner() -> None:
    task = start_shadow(_llm(), _inputs(), modes=ShadowModes(understand="shadow"), mission_id="m")
    record = await collect_shadow(task, LegacyDecision())
    assert record is not None and record["status"] == "ok" and "plan" not in record


async def test_a_failing_model_is_a_clarification_never_an_error() -> None:
    task = start_shadow(_llm(fail=True), _inputs(), modes=BOTH, mission_id="m")
    record = await collect_shadow(task, LegacyDecision())
    assert record is not None and record["status"] == "clarification"
    assert record["clarification"]["reason"] == "understanding_unavailable"


async def test_a_slow_shadow_is_cancelled_not_awaited() -> None:
    task = start_shadow(_llm(delay=5), _inputs(), modes=BOTH, mission_id="m")
    record = await collect_shadow(task, LegacyDecision(), timeout_s=0.05)
    assert record == {"status": "timeout"}
    assert task is not None and task.done()


async def test_answer_shadow_runs_plan_and_drafts() -> None:
    from seleric_swarm.contracts.pipeline import AnswerDraft, Claim, Paragraph, Ref
    from seleric_swarm.semantic import QueryResponse

    class Port:
        async def query(self, args):  # type: ignore[no-untyped-def]
            return QueryResponse(rows=({"zz_take": 42.0},))

    async def answer_llm(*, instructions: str, prompt: str):
        return AnswerDraft(status="completed", claims=(Claim(claim_id="t", ref=Ref(kind="result_set", id="q1"), label="Take", role="headline"),),
                           blocks=(Paragraph(kind="paragraph", text="Take was {t}."),)), {}

    inputs = ShadowInputs(question="take yesterday", as_of=AS_OF, read_model=MODEL, port=Port())  # type: ignore[arg-type]
    task = start_shadow(_llm(), inputs, modes=ShadowModes(understand="shadow", answer="shadow"), mission_id="m", answer_llm=answer_llm)
    record = await collect_shadow(task, LegacyDecision())
    assert record is not None and record["run"]["result_sets"] == 1
    assert record["answer"]["status"] == "completed" and record["answer"]["issues"] == []
