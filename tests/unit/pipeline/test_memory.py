"""M5.2: prior spec from the turn record, brand defaults as stated assumptions."""

from __future__ import annotations

from datetime import date

from seleric_swarm.contracts.pipeline import QuestionDraft
from seleric_swarm.pipeline import ShadowInputs, apply_brand_defaults, prior_spec, run_mission
from seleric_swarm.pipeline._shadow import spec_json
from tests.unit.pipeline.test_mission import DRAFT, _answer_llm, _understand
from tests.unit.pipeline.test_run_plan import AS_OF, MODEL, FactPort, spec

PREFS = {"default_window": {"unit": "day", "n": 7}}


def test_prior_spec_round_trip() -> None:
    s = spec("lookup")
    assert prior_spec({"v2_spec": spec_json(s)}) is not None
    assert prior_spec({"v2_spec": {"bad": 1}}) is None and prior_spec(None) is None


def test_brand_window_only_when_no_period() -> None:
    open_q = QuestionDraft(kind="analysis", shape="lookup")
    filled, notes = apply_brand_defaults(open_q, PREFS)
    assert filled.windows[0].expr.n == 7 and notes == ("brand_default:window=n=7,unit=day",)
    same, none = apply_brand_defaults(DRAFT, PREFS)
    assert same == DRAFT and none == ()


async def test_assumption_reaches_the_spec() -> None:
    no_period = DRAFT.model_copy(update={"windows": (), "names_period": False})
    inputs = ShadowInputs(question="take?", as_of=date(2026, 10, 10), as_of_time=AS_OF, read_model=MODEL,
                          port=FactPort(), brand_prefs=PREFS)  # type: ignore[arg-type]
    out = await run_mission(_understand(no_period), _answer_llm, inputs, mission_id="m")
    assert out.spec is not None and "brand_default:window=n=7,unit=day" in out.spec.assumptions
    assert out.spec.windows[0].end == date(2026, 10, 9)
