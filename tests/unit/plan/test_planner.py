"""M2.1 — template planner, through the public ``plan``.

Invented ``zz_*`` catalogue: every decision must come from the spec and the read model's
facts (aggregation, volume metric, carried dimensions, families), never from names.
"""

from __future__ import annotations

from datetime import date

import pytest

from seleric_swarm.contracts.pipeline import (
    AnalysisPlan,
    DeriveStep,
    EngineStep,
    EntitySpec,
    FilterSpec,
    MeasureSpec,
    QueryStep,
    QuestionSpec,
    RankingSpec,
    WindowSpec,
)
from seleric_swarm.plan import AGENT_TEMPLATE_ID, plan, plan_notes, template_ids
from seleric_swarm.semantic import SemanticReadModel

EVENT = WindowSpec(role="event", start=date(2026, 10, 1), end=date(2026, 10, 7))
BASELINE = WindowSpec(role="baseline", start=date(2026, 9, 24), end=date(2026, 9, 30))
LONG_BASELINE = WindowSpec(role="baseline", start=date(2026, 9, 1), end=date(2026, 9, 30))


def _model() -> SemanticReadModel:
    return SemanticReadModel.from_payload({
        "catalogue_version": "zz-v1",
        "metrics": [
            {"id": "zz_take", "label": "Take", "unit": "ZZU", "aggregation": "additive",
             "supported_dimensions": ["zz_zone", "zz_stall"]},
            {"id": "zz_visits", "label": "Visits", "unit": "count", "aggregation": "additive",
             "supported_dimensions": ["zz_zone", "zz_stall", "zz_route"]},
            {"id": "zz_take_rate", "label": "Take rate", "unit": "ratio", "aggregation": "ratio",
             "volume_metric": "zz_visits", "supported_dimensions": ["zz_zone", "zz_stall"]},
            {"id": "zz_baskets", "label": "Baskets", "unit": "count", "aggregation": "additive",
             "supported_dimensions": ["zz_zone"]},
        ],
        "dimensions": [
            {"id": "zz_zone"},
            {"id": "zz_stall"},
            {"id": "zz_route", "family": "zz_place", "family_rank": 1},
            {"id": "zz_zone_alias", "family": "zz_place", "family_rank": 2},
            {"id": "zz_day", "is_time": True},
        ],
    })


def _spec(**update) -> QuestionSpec:
    base = {
        "kind": "analysis",
        "shape": "lookup",
        "measures": (MeasureSpec(phrase="take", metric_id="zz_take"),),
        "filters": (),
        "entities": (),
        "breakdowns": (),
        "windows": (EVENT,),
        "grain": None,
        "ranking": None,
        "catalogue_version": "zz-v1",
    }
    base.update(update)
    return QuestionSpec(**base)


def _queries(p: AnalysisPlan) -> list[QueryStep]:
    return [s for s in p.steps if isinstance(s, QueryStep)]


def _derives(p: AnalysisPlan) -> list[DeriveStep]:
    return [s for s in p.steps if isinstance(s, DeriveStep)]


def test_lookup_is_one_query_per_metric_on_the_event_window() -> None:
    p = plan(_spec(measures=(MeasureSpec(phrase="take", metric_id="zz_take"),
                             MeasureSpec(phrase="visits", metric_id="zz_visits"))), _model())
    assert p.template_id == "lookup" and not p.needs_agent
    assert [(q.metric_id, q.window_role, q.breakdowns) for q in _queries(p)] == [
        ("zz_take", "event", ()), ("zz_visits", "event", ())]


def test_filters_ride_every_step_including_exclusions() -> None:
    keep = FilterSpec(dimension="zz_zone", operator="in", values=("north",))
    drop = FilterSpec(dimension="zz_stall", operator="not_in", values=("s9",))
    p = plan(_spec(filters=(keep, drop)), _model())
    assert _queries(p)[0].filters == (keep, drop)


def test_breakdown_slices_each_metric_by_what_it_carries_and_adds_the_volume_beside_a_ratio() -> None:
    p = plan(_spec(shape="breakdown", breakdowns=("zz_stall",),
                   measures=(MeasureSpec(phrase="take rate", metric_id="zz_take_rate"),)), _model())
    assert p.template_id == "breakdown"
    assert [(q.metric_id, q.breakdowns) for q in _queries(p)] == [
        ("zz_take_rate", ("zz_stall",)), ("zz_visits", ("zz_stall",))]
    assert any("zz_visits fetched beside zz_take_rate" in n for n in plan_notes(
        _spec(shape="breakdown", breakdowns=("zz_stall",),
              measures=(MeasureSpec(phrase="take rate", metric_id="zz_take_rate"),)), _model()))


def test_a_breakdown_the_metric_cannot_carry_is_left_off_its_step() -> None:
    p = plan(_spec(shape="breakdown", breakdowns=("zz_stall",),
                   measures=(MeasureSpec(phrase="baskets", metric_id="zz_baskets"),
                             MeasureSpec(phrase="take", metric_id="zz_take"))), _model())
    assert {q.metric_id: q.breakdowns for q in _queries(p)} == {"zz_baskets": (), "zz_take": ("zz_stall",)}


def test_breakdown_ranking_is_typed_on_the_rank_metric_step() -> None:
    ranking = RankingSpec(by_metric_id="zz_take", order="desc", limit=3)
    p = plan(_spec(shape="breakdown", breakdowns=("zz_stall",), ranking=ranking,
                   measures=(MeasureSpec(phrase="visits", metric_id="zz_visits"),)), _model())
    by_metric = {q.metric_id: q for q in _queries(p)}
    assert by_metric["zz_take"].ranking == ranking
    assert by_metric["zz_visits"].ranking is None


def test_trend_reads_by_day_when_no_grain_named_and_keeps_a_named_grain() -> None:
    assert _queries(plan(_spec(shape="trend"), _model()))[0].grain == "day"
    assert _queries(plan(_spec(shape="trend", grain="week"), _model()))[0].grain == "week"


def test_period_comparison_queries_both_windows_then_derives_the_change() -> None:
    p = plan(_spec(shape="period_comparison", windows=(EVENT, BASELINE)), _model())
    assert p.template_id == "period_comparison"
    assert [(q.metric_id, q.window_role) for q in _queries(p)] == [("zz_take", "baseline"), ("zz_take", "event")]
    ops = _derives(p)
    assert [d.op for d in ops] == ["delta", "pct_change"]
    assert all(d.inputs == ("q1", "q2") and d.params == {} for d in ops)


def test_unequal_windows_compare_per_day() -> None:
    p = plan(_spec(shape="period_comparison", windows=(EVENT, LONG_BASELINE)), _model())
    assert all(d.params == {"per_day": True} for d in _derives(p))


def test_a_comparison_with_today_running_counts_elapsed_hours_for_additive_metrics_only() -> None:
    spec = _spec(shape="period_comparison", windows=(EVENT, BASELINE),
                 measures=(MeasureSpec(phrase="take", metric_id="zz_take"),
                           MeasureSpec(phrase="rate", metric_id="zz_take_rate")))
    steps = _queries(plan(spec, _model(), as_of=date(2026, 10, 7)))
    assert {(q.metric_id, q.elapsed_only) for q in steps} == {("zz_take", True), ("zz_take_rate", False)}
    assert not any(q.elapsed_only for q in _queries(plan(spec, _model(), as_of=date(2026, 10, 9))))


def test_a_comparison_naming_no_measure_uses_the_injected_headline() -> None:
    spec = _spec(shape="period_comparison", windows=(EVENT, BASELINE), measures=())
    assert plan(spec, _model()).needs_agent
    p = plan(spec, _model(), headline_metric_ids=("zz_take", "zz_gone"))
    assert [q.metric_id for q in _queries(p)] == ["zz_take", "zz_take"]


def test_entity_comparison_ranks_then_compares_the_same_entities() -> None:
    ranking = RankingSpec(by_metric_id="zz_take", order="desc", limit=5)
    spec = _spec(shape="entity_comparison", windows=(EVENT, BASELINE), ranking=ranking,
                 breakdowns=("zz_stall",), measures=(MeasureSpec(phrase="visits", metric_id="zz_visits"),))
    p = plan(spec, _model())
    assert p.template_id == "entity_comparison"
    rank, *rest = _queries(p)
    assert (rank.step_id, rank.metric_id, rank.breakdowns, rank.ranking) == ("rank", "zz_take", ("zz_stall",), ranking)
    assert {(q.metric_id, q.window_role) for q in rest} == {
        ("zz_take", "event"), ("zz_take", "baseline"), ("zz_visits", "event"), ("zz_visits", "baseline")}
    assert all(q.entities_from_step == "rank" and q.breakdowns == ("zz_stall",) for q in rest)
    assert len(_derives(p)) == 4


def test_a_ranked_entity_over_two_windows_is_an_entity_comparison_whatever_the_label() -> None:
    ranking = RankingSpec(by_metric_id="zz_take", order="desc", limit=5)
    spec = _spec(shape="period_comparison", windows=(EVENT, BASELINE), ranking=ranking,
                 entities=(EntitySpec(dimension="zz_stall", values=("s1", "s2")),))
    assert plan(spec, _model()).template_id == "entity_comparison"


def test_an_entity_the_rank_metric_cannot_carry_is_replaced_by_a_breakdown_it_can() -> None:
    ranking = RankingSpec(by_metric_id="zz_take", order="desc", limit=5)
    spec = _spec(shape="entity_comparison", windows=(EVENT, BASELINE), ranking=ranking,
                 entities=(EntitySpec(dimension="zz_route", values=("r1",)),), breakdowns=("zz_stall",))
    p = plan(spec, _model())
    assert _queries(p)[0].breakdowns == ("zz_stall",)


def test_another_listed_entity_kind_is_its_own_ranking() -> None:
    """Never one cross-product query; a dimension reached through a conformed sibling is kept as asked
    (the executor conforms it), one the rank metric cannot reach at all is dropped."""
    ranking = RankingSpec(by_metric_id="zz_visits", order="desc", limit=5)
    spec = _spec(shape="entity_comparison", windows=(EVENT, BASELINE), ranking=ranking,
                 breakdowns=("zz_stall", "zz_zone_alias", "zz_day"), measures=())
    ranks = [q for q in _queries(plan(spec, _model())) if q.ranking is not None]
    assert [q.breakdowns for q in ranks] == [("zz_stall",), ("zz_zone_alias",)]


def test_why_hands_the_outcome_and_the_named_drivers_to_investigate() -> None:
    spec = _spec(shape="why_single_metric", windows=(EVENT, BASELINE), direction="down",
                 measures=(MeasureSpec(phrase="take", metric_id="zz_take"),
                           MeasureSpec(phrase="visits", metric_id="zz_visits")))
    p = plan(spec, _model())
    (step,) = p.steps
    assert p.template_id == "why" and isinstance(step, EngineStep) and step.engine == "investigate"
    assert step.params["metric_id"] == "zz_take" and step.params["drivers"] == ["zz_visits"]
    assert step.params["direction"] == "down" and len(step.params["windows"]) == 2


def test_why_while_today_runs_is_compared_instead() -> None:
    spec = _spec(shape="why_single_metric", windows=(EVENT, BASELINE))
    assert plan(spec, _model(), as_of=date(2026, 10, 7)).template_id == "period_comparison"


def test_composition_and_exploration_are_engine_steps() -> None:
    comp = plan(_spec(shape="composition"), _model())
    assert comp.template_id == "composition" and comp.steps[0].engine == "composition"  # type: ignore[union-attr]
    explore = plan(_spec(kind="exploration", shape="other"), _model())
    assert explore.template_id == "exploration" and explore.steps[0].engine == "explore"  # type: ignore[union-attr]


def test_funnel_derives_each_stage_share_of_the_one_before() -> None:
    p = plan(_spec(shape="funnel", measures=(MeasureSpec(phrase="visits", metric_id="zz_visits"),
                                             MeasureSpec(phrase="baskets", metric_id="zz_baskets"))), _model())
    assert p.template_id == "funnel"
    (share,) = _derives(p)
    assert share.op == "share" and share.inputs == ("q2", "q1")


@pytest.mark.parametrize("spec", [
    _spec(shape="other"),
    _spec(kind="forecast"),
    _spec(kind="conversation", measures=()),
    _spec(measures=(MeasureSpec(phrase="gone", metric_id="zz_gone"),)),
    _spec(shape="period_comparison"),  # one window: nothing to compare
])
def test_what_no_template_fits_goes_to_the_agent(spec: QuestionSpec) -> None:
    p = plan(spec, _model())
    assert p.needs_agent and p.template_id == AGENT_TEMPLATE_ID and p.steps == ()


def test_plans_are_deterministic_and_registry_lists_its_shapes() -> None:
    spec = _spec(shape="period_comparison", windows=(EVENT, BASELINE))
    assert plan(spec, _model()) == plan(spec, _model())
    assert {"lookup", "breakdown", "trend", "period_comparison", "entity_comparison",
            "composition", "funnel", "why", "exploration"} <= set(template_ids())


def test_a_catalogue_rename_needs_no_code_change() -> None:
    """Semantic pickup: the same plan over renamed ids, read entirely from the read model."""
    payload = {
        "metrics": [{"id": "zz_new_take", "aggregation": "ratio", "volume_metric": "zz_new_visits",
                     "supported_dimensions": ["zz_new_stall"]},
                    {"id": "zz_new_visits", "aggregation": "additive", "supported_dimensions": ["zz_new_stall"]}],
        "dimensions": [{"id": "zz_new_stall"}],
    }
    spec = _spec(shape="breakdown", breakdowns=("zz_new_stall",),
                 measures=(MeasureSpec(phrase="take", metric_id="zz_new_take"),))
    p = plan(spec, SemanticReadModel.from_payload(payload))
    assert [q.metric_id for q in _queries(p)] == ["zz_new_take", "zz_new_visits"]
