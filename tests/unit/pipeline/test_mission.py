"""run_mission (v2 enforce path): answers, clarifies, or falls back with a reason."""

from __future__ import annotations

from datetime import date

from seleric_swarm.contracts.pipeline import (
    AnswerDraft,
    Claim,
    Clarification,
    MeasurePhrase,
    Paragraph,
    QuestionDraft,
    Ref,
    WindowExpr,
    WindowSlot,
)
from seleric_swarm.pipeline import ShadowInputs, run_mission
from tests.unit.pipeline.test_run_plan import AS_OF, MODEL, FactPort

DRAFT = QuestionDraft(
    kind="analysis", shape="lookup",
    measures=(MeasurePhrase(words="take", metric_id="zz_take"),),
    windows=(WindowSlot(role="event", expr=WindowExpr(name="yesterday"), source_span="yesterday"),),
    names_period=True,
)


def _understand(output=DRAFT):
    async def call(*, instructions: str, prompt: str):
        return output, {}
    return call


async def _answer_llm(*, instructions: str, prompt: str):
    return AnswerDraft(status="completed",
                       claims=(Claim(claim_id="t", ref=Ref(kind="result_set", id="q1"), label="Take", role="total"),),
                       blocks=(Paragraph(kind="paragraph", text="Take was {t}."),), next_step="By zone?"), {}


def _inputs(port=None) -> ShadowInputs:
    return ShadowInputs(question="take yesterday", as_of=date(2026, 10, 10), as_of_time=AS_OF, read_model=MODEL,
                        port=port if port is not None else FactPort())  # type: ignore[arg-type]


async def test_answers_through_v2() -> None:
    out = await run_mission(_understand(), _answer_llm, _inputs(), mission_id="m")
    assert out.status == "answered", out.trace
    assert out.doc is not None and out.doc.rendered_markdown.startswith("Take was INR 310")
    assert out.turn is not None and out.turn.offer == "By zone?"
    assert out.trace["plan"]["template_id"] == "lookup"


async def test_falls_back_without_port_or_on_other_kinds() -> None:
    no_port = ShadowInputs(question="q", as_of=date(2026, 10, 10), read_model=MODEL)
    assert (await run_mission(_understand(), _answer_llm, no_port, mission_id="m")).reason == "v2_unavailable"
    chat = DRAFT.model_copy(update={"kind": "conversation"})
    assert (await run_mission(_understand(chat), _answer_llm, _inputs(), mission_id="m")).reason == "kind:conversation"


async def test_clarification_only_in_enforce() -> None:
    c = Clarification(reason="ambiguous_value", question_to_user="Which zone?")
    off = await run_mission(_understand(c), _answer_llm, _inputs(), mission_id="m")
    assert off.status == "fallback" and off.reason == "clarification:ambiguous_value"
    on = await run_mission(_understand(c), _answer_llm, _inputs(), mission_id="m", clarify_mode="enforce")
    assert on.status == "clarify" and on.clarification == c


async def test_failed_answer_falls_back() -> None:
    async def broken(*, instructions: str, prompt: str):
        raise RuntimeError("down")

    out = await run_mission(_understand(), broken, _inputs(), mission_id="m")
    assert out.status == "fallback" and out.reason == "answer_failed"
