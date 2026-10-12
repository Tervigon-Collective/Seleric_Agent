"""Intent fidelity: the v2 path never answers a question it has rewritten into another one.

Live MS3-bb9e01b76b: "product sales analysis for the month of October" → a measureless draft →
headline exploration → 77 daily net-sales rows and ad creatives presented as product drivers.
"""

from __future__ import annotations

from datetime import date

from seleric_swarm.answer import Evidence, verify
from seleric_swarm.answer._verify import OFF_SCOPE_CODES
from seleric_swarm.contracts.pipeline import (
    AnswerDraft,
    Claim,
    MeasurePhrase,
    MeasureSpec,
    Paragraph,
    QuestionDraft,
    QuestionSpec,
    Ref,
    ResultRow,
    ResultSet,
    WindowExpr,
    WindowSlot,
    WindowSpec,
)
from seleric_swarm.pipeline import ShadowInputs, run_mission
from seleric_swarm.pipeline._fidelity import (
    explicit_metric_ids,
    intent_lost,
    scope_axes,
    scope_uncovered,
)
from seleric_swarm.pipeline._shadow import underspecified_as_exploration
from seleric_swarm.semantic import SemanticReadModel
from seleric_swarm.spec import SpecCandidates, compile_question_spec
from seleric_swarm.understand._call import call_understand

MODEL = SemanticReadModel.from_payload({
    "catalogue_version": "zz-v1",
    "metrics": [
        {"id": "zz_sales", "label": "Sales", "aggregation": "additive",
         "supported_dimensions": ["zz_item", "zz_basket_item", "zz_creative"]},
        {"id": "zz_spend", "label": "Spend", "aggregation": "additive", "supported_dimensions": ["zz_creative"]},
    ],
    "dimensions": [
        {"id": "zz_item", "family": "item", "family_rank": 0},
        {"id": "zz_basket_item", "family": "item", "family_rank": 1},
        {"id": "zz_creative"},
    ],
})
EVENT = WindowSpec(role="event", start=date(2026, 10, 1), end=date(2026, 10, 11))
OCTOBER = WindowSlot(role="event", expr=WindowExpr(ending="yesterday"), source_span="the month of October")
HEADLINE = ("zz_sales", "zz_spend")


def _spec(**update) -> QuestionSpec:
    base = QuestionSpec(kind="analysis", shape="breakdown", measures=(MeasureSpec(phrase="sales", metric_id="zz_sales"),),
                        filters=(), entities=(), breakdowns=(), windows=(EVENT,), grain=None, ranking=None,
                        catalogue_version="zz-v1")
    return base.model_copy(update=update)


def _rs(ref: str, metric: str, breakdowns: tuple[str, ...], rows=(), error=None) -> ResultSet:
    return ResultSet(ref=Ref(kind="result_set", id=ref), step_id=ref, metric_id=metric, unit="INR", window=EVENT,
                     breakdowns=breakdowns, filters=(), rows=tuple(rows), error=error)


# --- gate ---------------------------------------------------------------------------------------

def test_a_dropped_entity_dimension_is_lost_intent() -> None:
    asked = QuestionDraft(kind="analysis", shape="breakdown", entity_dimension="zz_item")
    assert intent_lost(asked, _spec(), MODEL) == ["dimension:zz_item"]


def test_a_conformed_sibling_carries_the_asked_dimension() -> None:
    asked = QuestionDraft(kind="analysis", shape="breakdown", breakdown_dimensions=("zz_item",))
    assert intent_lost(asked, _spec(breakdowns=("zz_basket_item",)), MODEL) == []


def test_defaulted_measures_on_a_scoped_or_unread_question_are_lost_intent() -> None:
    asked = QuestionDraft(kind="analysis", shape="breakdown", needs_clarification="Which sales metric?")
    lost = intent_lost(asked, _spec(), MODEL, defaulted_measures=True, axes=(("scope", "product"),))
    assert lost == ["axes:scope=product", "unread:model_asked"]


def test_an_overview_asking_which_year_still_takes_the_headline() -> None:
    asked = QuestionDraft(kind="overview", names_period=True, needs_clarification="Which year?")
    assert intent_lost(asked, _spec(), MODEL, defaulted_measures=True) == []


def test_a_spec_with_no_measures_cannot_run() -> None:
    asked = QuestionDraft(kind="analysis", shape="breakdown", entity_dimension="zz_item")
    assert intent_lost(asked, _spec(measures=(), breakdowns=("zz_item",)), MODEL) == ["measures:none"]


def test_only_scope_axes_narrow_the_question() -> None:
    assert scope_axes((("date", "order"), ("scope", "product"))) == (("scope", "product"),)


# --- no silent downgrade ------------------------------------------------------------------------

def test_open_measureless_analysis_is_still_explored() -> None:
    draft = QuestionDraft(kind="analysis", shape="lookup", names_period=True)
    assert underspecified_as_exploration(draft, HEADLINE, read_model=MODEL).kind == "exploration"


def test_scoped_unread_or_axis_bound_drafts_keep_their_reading() -> None:
    base = QuestionDraft(kind="analysis", shape="breakdown", names_period=True)
    for draft, axes in (
        (base.model_copy(update={"entity_dimension": "zz_item"}), ()),
        (base.model_copy(update={"needs_clarification": "Which sales metric?"}), ()),
        (base, (("scope", "product"),)),
    ):
        kept = underspecified_as_exploration(draft, HEADLINE, read_model=MODEL, scope_axes=axes)
        assert kept is draft


# --- compile ------------------------------------------------------------------------------------

def test_breakdown_naming_only_the_entity_family_breaks_down_by_it() -> None:
    draft = QuestionDraft(kind="analysis", shape="breakdown", entity_dimension="zz_item",
                          measures=(MeasurePhrase(words="sales", metric_id="zz_sales"),), windows=(OCTOBER,))
    spec = compile_question_spec(draft, SpecCandidates(), MODEL, None, date(2026, 10, 12), clarify_mode="off")
    assert isinstance(spec, QuestionSpec)
    assert spec.breakdowns == ("zz_item",)


# --- mission ------------------------------------------------------------------------------------

def _understand(output):
    async def call(*, instructions: str, prompt: str):
        return output, {}
    return call


async def _never_answer(*, instructions: str, prompt: str):
    raise AssertionError("a lost question must not reach the answer stage")


class _Port:
    async def query(self, args):  # pragma: no cover - the gate stops before any read
        raise AssertionError("a lost question must not be queried")


def _inputs(resolution=None) -> ShadowInputs:
    return ShadowInputs(question="product sales analysis for the month of October", as_of=date(2026, 10, 12),
                        read_model=MODEL, port=_Port(), headline_metric_ids=HEADLINE,  # type: ignore[arg-type]
                        resolution=resolution)


async def test_recorded_measureless_draft_falls_back_instead_of_exploring() -> None:
    recorded = QuestionDraft(kind="analysis", shape="breakdown", windows=(OCTOBER,), names_period=True,
                             needs_clarification="Which sales metric(s) do you mean by 'product sales'?")
    out = await run_mission(_understand(recorded), _never_answer, _inputs(), mission_id="m")
    assert out.status == "fallback"
    assert out.reason.startswith("intent_lost:") and "measures:none" in out.reason
    assert out.spec is not None and out.spec.kind != "exploration"


async def test_entity_only_draft_falls_back_instead_of_exploring() -> None:
    draft = QuestionDraft(kind="analysis", shape="breakdown", entity_dimension="zz_item", windows=(OCTOBER,),
                          names_period=True)
    out = await run_mission(_understand(draft), _never_answer, _inputs(), mission_id="m")
    assert out.status == "fallback" and out.reason == "intent_lost:measures:none"
    assert out.spec is not None and out.spec.breakdowns == ("zz_item",)


async def test_axis_bound_measureless_draft_falls_back() -> None:
    draft = QuestionDraft(kind="analysis", shape="breakdown", windows=(OCTOBER,), names_period=True)
    out = await run_mission(_understand(draft), _never_answer, _inputs({"axes": {"scope": "product"}}), mission_id="m")
    assert out.status == "fallback" and "measures:none" in out.reason


# --- coverage -----------------------------------------------------------------------------------

def test_scope_uncovered_needs_a_successful_read_of_the_family_and_named_metrics() -> None:
    ok_sibling = _rs("q1", "zz_sales", ("zz_basket_item",))
    failed = _rs("q2", "zz_sales", ("zz_item",), error={"stage": "execute", "code": "QUERY_FAILED", "message": "x"})
    assert scope_uncovered(("zz_item",), ("zz_sales",), [ok_sibling], MODEL) == []
    assert scope_uncovered(("zz_item",), ("zz_spend",), [failed], MODEL) == ["dimension:zz_item", "metric:zz_spend"]


def test_explicit_metrics_are_the_ones_the_user_wrote() -> None:
    draft = QuestionDraft(measures=(MeasurePhrase(words="Sales", metric_id="zz_sales"),
                                    MeasurePhrase(words="Spend", metric_id="zz_spend")))
    assert explicit_metric_ids("what were sales by item", draft, MODEL) == ("zz_sales",)


# --- verify -------------------------------------------------------------------------------------

def _evidence() -> tuple[Evidence, ResultSet, ResultSet]:
    by_item = _rs("q1", "zz_sales", ("zz_item",), rows=(ResultRow(dimensions={"zz_item": "boots"}, value=10.0,
                                                                  evidence_id="ev1"),))
    by_creative = _rs("q2", "zz_sales", ("zz_creative",), rows=(ResultRow(dimensions={"zz_creative": "TH-149"},
                                                                          value=5.0, evidence_id="ev2"),))
    return Evidence([by_item, by_creative]), by_item, by_creative


def _draft(claim_ref: str, row: dict[str, str], text: str, next_step: str = "") -> AnswerDraft:
    return AnswerDraft(status="completed",
                       claims=(Claim(claim_id="c", ref=Ref(kind="result_set", id=claim_ref), row_key=row,
                                     label="Sales", role="headline"),),
                       blocks=(Paragraph(kind="paragraph", text=text),), next_step=next_step)


def test_a_claim_on_another_entity_is_off_scope() -> None:
    evidence, _, _ = _evidence()
    spec = _spec(breakdowns=("zz_item",))
    v = verify(_draft("q2", {"zz_creative": "TH-149"}, "Top seller was {c}."), spec, evidence, read_model=MODEL)
    assert "claim_off_entity" in v.codes() and OFF_SCOPE_CODES & set(v.codes())
    on = verify(_draft("q1", {"zz_item": "boots"}, "Top seller was {c}."), spec, evidence, read_model=MODEL)
    assert not OFF_SCOPE_CODES & set(on.codes())


def test_prose_and_next_step_must_stay_on_the_question_entity() -> None:
    evidence, by_item, _ = _evidence()
    spec = _spec(breakdowns=("zz_item",))
    v = verify(_draft("q1", {"zz_item": "boots"}, "Top seller was {c}; zz_basket_item agrees.",
                      next_step="Break down by zz_creative?"), spec, evidence, read_model=MODEL)
    assert "next_step_off_entity" in v.codes()
    assert "ungrounded_dimension" not in v.codes()
    stray = verify(_draft("q1", {"zz_item": "boots"}, "Top seller was {c}; see zz_creative."), _spec(breakdowns=("zz_item",)),
                   Evidence([by_item]), read_model=MODEL)
    assert "ungrounded_dimension" in stray.codes()


# --- understand ---------------------------------------------------------------------------------

async def test_understand_revises_a_measureless_analysis_once() -> None:
    empty = QuestionDraft(kind="analysis", shape="breakdown")
    filled = empty.model_copy(update={"measures": (MeasurePhrase(words="Sales", metric_id="zz_sales"),)})
    prompts: list[str] = []

    async def llm(*, instructions: str, prompt: str):
        prompts.append(prompt)
        return (empty if len(prompts) == 1 else filled), {}

    out, stats = await call_understand(llm, instructions="i", prompt="q")
    assert out == filled and stats["revised"] == "no_measures"
    assert len(prompts) == 2 and "REVISE" in prompts[1]


async def test_understand_keeps_the_first_draft_when_the_revision_is_still_empty() -> None:
    empty = QuestionDraft(kind="analysis", shape="breakdown", needs_clarification="Which metric?")
    calls = iter([empty, QuestionDraft(kind="analysis")])

    async def llm2(*, instructions: str, prompt: str):
        return next(calls), {}

    out, _ = await call_understand(llm2, instructions="i", prompt="q")
    assert out == empty
