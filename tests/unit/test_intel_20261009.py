"""Regressions from the 2026-10-09 audit of failed steps, reconciliation and diagnosis answers.

- "cost per order (CPA)" read "per order" as a required breakdown by every order_* dimension: three forced
  revisions and a shipped answer that dropped the asked comparison (MS3-e4ba59ad63). Breakdowns now come from
  the understanding's own reading of the question.
- A "net profit waterfall" built from metrics on different views showed a 1.6M "residual" (MS3-9781c608fc):
  break_down_metric walks the catalogue's verified composition on one view, so the lines reconcile.
- Written date ranges ("between X and Y", "X..Y vs A..B") were read as single days.
- Offsetting parts were reported as "+694% / −594% of the change".
- The model chain sent the primary's reasoning_effort to its fallback.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.agent.scope import scope_from_dimensions
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot
from seleric_swarm.services.time_range import window_from_query
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import composition, diagnosis

AS_OF = datetime(2026, 10, 9, 12, tzinfo=UTC)


# ---- breakdown scope from the understanding --------------------------------------------------------------


def test_a_measures_own_words_are_not_a_breakdown() -> None:
    dims = frozenset({"order_date", "order_id", "order_status", "is_first_order", "campaign_name"})
    scope = scope_from_dimensions(
        ["campaign_name"], alias_index={}, dimension_ids=dims, is_time_dimension=lambda d: d == "order_date"
    )
    assert scope.breakdowns == frozenset({frozenset({"campaign_name"})})


def test_no_understood_breakdown_requires_none_and_time_is_the_grains_job() -> None:
    dims = frozenset({"order_date", "campaign_name"})
    assert not scope_from_dimensions([], alias_index={}, dimension_ids=dims).breakdowns
    scope = scope_from_dimensions(["order_date"], alias_index={}, dimension_ids=dims,
                                  is_time_dimension=lambda d: d == "order_date")
    assert not scope.breakdowns


# ---- written date ranges --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("orders between 2026-09-01 and 2026-09-30", ("absolute", "2026-09-01", "2026-09-30", None, None)),
        ("spend over 2026-10-07..2026-10-08 and 2026-10-06..2026-10-07 for comparison",
         ("comparison", "2026-10-07", "2026-10-08", "2026-10-06", "2026-10-07")),
        ("net sales 2026-10-05 to 2026-10-08 compared with last week",
         ("comparison", "2026-10-05", "2026-10-08", "2026-09-28", "2026-10-04")),
        ("On 2026-10-02 we recorded a loss versus 2026-10-01",
         ("comparison", "2026-10-02", "2026-10-02", "2026-10-01", "2026-10-01")),
    ],
)
def test_written_ranges_are_spans(question: str, expected: tuple[Any, ...]) -> None:
    w = window_from_query(question, "Asia/Kolkata", "2026-10-09T17:00:00+05:30")
    assert w is not None
    assert (w.kind, w.start, w.end, w.start_b, w.end_b) == expected


# ---- break_down_metric ----------------------------------------------------------------------------------


_DEFS = {
    "np": {"display_name": "Net profit", "unit": "INR",
           "formula": {"composition": [{"metric": "cm", "sign": 1}, {"metric": "ad", "sign": -1}]}},
    "cm": {"display_name": "Contribution margin", "unit": "INR",
           "formula": {"composition": [{"metric": "ns", "sign": 1}, {"metric": "cogs", "sign": -1}]}},
    "ns": {"display_name": "Net sales", "unit": "INR", "formula": {}},
    "cogs": {"display_name": "COGS", "unit": "INR", "formula": {}},
    "ad": {"display_name": "Ad spend", "unit": "INR", "formula": {}},
    "other": {"display_name": "Other", "unit": "INR", "formula": {}},
}


class _Mcp:
    def __init__(self, totals: dict[str, dict[str, float]]) -> None:
        self.totals = totals
        self.calls: list[dict[str, Any]] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(arguments)
        start = arguments["time_range"]["start"]
        vals = self.totals[start]
        return {"rows": [{m: vals[m] for m in arguments["measures"] if m in vals}], "provenance": {"currency": "INR"}}


class _Ctx:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _deps(mcp: _Mcp) -> SelericDeps:
    return SelericDeps(
        mission_id="m1", as_of=AS_OF,
        principal=Principal(principal_id="p", workspace_id="w", user_id="u"),
        thread_id="t", run_id="r", trace_id="tr", context=ContextBundle(), mcp_client=mcp,
        artifact_store=InMemoryArtifactStore(), limits=ExecutionLimits(), catalogue=CatalogueSnapshot(),
    )


@pytest.fixture
def defs(monkeypatch: pytest.MonkeyPatch) -> None:
    async def all_definitions(ctx: Any) -> dict[str, Any]:
        return _DEFS

    monkeypatch.setattr(diagnosis, "_all_definitions", all_definitions)


async def test_a_breakdown_walks_the_composition_and_reconciles(defs: None) -> None:
    mcp = _Mcp({"2026-10-01": {"np": -20.0, "cm": 80.0, "ns": 100.0, "cogs": 20.0, "ad": 100.0}})
    res = await composition.break_down_metric(
        _Ctx(_deps(mcp)), "np", period_start=datetime(2026, 10, 1, tzinfo=UTC), period_end=datetime(2026, 10, 7, tzinfo=UTC)
    )
    assert res.success
    assert "Reconciled" in res.summary
    # one query per period, every line on it
    assert len(mcp.calls) == 1 and set(mcp.calls[0]["measures"]) == {"np", "cm", "ns", "cogs", "ad"}
    assert "+ Net sales 100.00 − COGS 20.00 − Ad spend 100.00 = -20.00" in res.summary


async def test_a_gap_is_reported_never_absorbed(defs: None) -> None:
    mcp = _Mcp({"2026-10-01": {"np": -25.0, "cm": 80.0, "ns": 100.0, "cogs": 20.0, "ad": 100.0}})
    res = await composition.break_down_metric(_Ctx(_deps(mcp)), "np", period_start=datetime(2026, 10, 1, tzinfo=UTC))
    assert res.success and "NOT reconciled" in res.summary


async def test_a_bridge_gives_each_lines_effect_on_the_total(defs: None) -> None:
    mcp = _Mcp({
        "2026-10-02": {"np": -20.0, "cm": 80.0, "ns": 100.0, "cogs": 20.0, "ad": 100.0},
        "2026-10-01": {"np": 10.0, "cm": 90.0, "ns": 110.0, "cogs": 20.0, "ad": 80.0},
    })
    res = await composition.break_down_metric(
        _Ctx(_deps(mcp)), "np", period_start=datetime(2026, 10, 2, tzinfo=UTC),
        compare_start=datetime(2026, 10, 1, tzinfo=UTC),
    )
    assert res.success
    # ad spend rose 20 → its effect on profit is −20; net sales fell 10 → −10; total change −30
    assert "Ad spend -20.00" in res.summary and "Net sales -10.00" in res.summary
    assert "sum to -30.00" in res.summary


async def test_a_metric_without_composition_names_those_with_one(defs: None) -> None:
    res = await composition.break_down_metric(_Ctx(_deps(_Mcp({}))), "other")
    assert not res.success and "np (Net profit)" in res.summary


# ---- diagnosis wording ----------------------------------------------------------------------------------


def test_offsetting_parts_are_stated_in_the_outcomes_units() -> None:
    from seleric_swarm.causal import diagnosis as engine

    assert engine._offsetting([6.94, -5.94])
    assert not engine._offsetting([0.7, 0.3])
    lineage = {"roas": engine.MetricMeta("roas", unit="x")}
    assert engine._effect(-0.55, "roas", lineage).startswith("−")


def test_named_drivers_are_each_reported_largest_move_first() -> None:
    from seleric_swarm.causal import diagnosis as engine

    days = [date(2026, 10, 8)]
    ref = [date(2026, 10, 1), date(2026, 10, 2)]
    series = {
        "cpm": {date(2026, 10, 8): 150.0, date(2026, 10, 1): 100.0, date(2026, 10, 2): 100.0},
        "ctr": {date(2026, 10, 8): 0.011, date(2026, 10, 1): 0.010, date(2026, 10, 2): 0.010},
    }
    report = engine.DiagnosisReport.__new__(engine.DiagnosisReport)
    report.drivers = []
    text, figures = diagnosis._named_driver_lines(["ctr", "cpm", "missing"], series, days, ref, {}, report)
    assert figures["cpm | event average per day"] == 150.0 and figures["cpm | reference average per day"] == 100.0
    assert text.index("cpm") < text.index("ctr")
    assert "missing" in text and "could not be checked" in text


# ---- model chain ----------------------------------------------------------------------------------------


def test_reasoning_effort_is_the_primarys_only(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from seleric_swarm.agent import model as M
    from seleric_swarm.config.settings import Settings

    seen: list[tuple[str, Any]] = []
    real = M.HealthGatedChatModel

    def spy(name: str, **kw: Any) -> Any:
        seen.append((name, kw.get("settings")))
        return real(name, **kw)

    monkeypatch.setattr(M, "HealthGatedChatModel", spy)
    monkeypatch.setenv("AZURE_OPENAI_STRONG_REASONING_EFFORT", "low")
    settings = Settings(
        llm_provider="azure_openai_compatible", azure_openai_api_key="k", azure_openai_endpoint="https://x",
        azure_openai_models=json.dumps(["primary", "fallback"]), azure_openai_fast_model="",
    )
    M.resolve_v3_model(settings)
    assert [n for n, _ in seen] == ["primary", "fallback"]
    assert seen[0][1] is not None and seen[1][1] is None


def test_a_reworded_measure_phrase_does_not_leak_its_words_into_the_question_context() -> None:
    from seleric_swarm.agent.runner import _outside_measures

    q = "Why did CAC increase yesterday? Check whether the change came from CPM or product mix."
    context = _outside_measures(q, ["customer acquisition cost", "CPM", "product mix (changes in which products were sold)"])
    assert "product" not in context.lower().split()
    assert "yesterday?" in context.split()


def test_a_ratio_splits_exactly_through_the_rates_the_user_named() -> None:
    """CAC 2026-10-07 → 10-08 (ClickHouse): spend 71,171.66 → 69,847.63, impressions 204,701 → 189,255, clicks
    4,854 → 5,253, new customers 43 → 40. CPM +6.1% and CTR +17% (cheaper clicks) against fewer new customers per
    click: CAC +5.5%."""
    import random
    from datetime import timedelta

    from seleric_swarm.causal import diagnosis as engine

    def m(i: str, agg: str, deps: tuple[str, ...] = (), unit: str = "") -> engine.MetricMeta:
        return engine.MetricMeta(i, aggregation=agg, depends_on=deps, label=i.upper(), unit=unit)

    lineage = {
        "spend": m("spend", "additive"), "impr": m("impr", "additive"), "clicks": m("clicks", "additive"),
        "new": m("new", "additive"),
        "cac": m("cac", "ratio", ("spend", "new")), "cpm": m("cpm", "ratio", ("spend", "impr")),
        "ctr": m("ctr", "ratio", ("clicks", "impr")), "cpc": m("cpc", "ratio", ("spend", "clicks")),
    }
    rng = random.Random(7)
    series: dict[str, dict[date, float]] = {k: {} for k in lineage}
    day0 = date(2026, 9, 1)
    for i in range(37):
        d = day0 + timedelta(days=i)
        sp, im = 60000 + rng.random() * 15000, 180000 + rng.random() * 30000
        cl, nw = im * (0.022 + rng.random() * 0.006), 35 + rng.randint(0, 12)
        series["spend"][d], series["impr"][d], series["clicks"][d], series["new"][d] = sp, im, cl, nw
    for d, (sp, im, cl, nw) in {date(2026, 10, 7): (71171.66, 204701, 4854, 43),
                                date(2026, 10, 8): (69847.63, 189255, 5253, 40)}.items():
        series["spend"][d], series["impr"][d], series["clicks"][d], series["new"][d] = sp, im, cl, nw
    for d in series["spend"]:
        sp, im, cl, nw = (series[k][d] for k in ("spend", "impr", "clicks", "new"))
        series["cac"][d], series["cpm"][d] = sp / nw, sp * 1000 / im
        series["ctr"][d], series["cpc"][d] = cl / im, sp / cl
    history = sorted(d for d in series["cac"] if d < date(2026, 10, 8))
    text, figures = diagnosis._ratio_split(
        "cac", ["cpm", "ctr", "cpc", "checkout_rate"], lineage, series, history, [date(2026, 10, 8)], [date(2026, 10, 7)]
    )
    assert "EXACT SPLIT" in text
    assert abs(figures["cac | change %"] - 5.5) < 0.1
    # top level: cost per click against clicks per new customer (the closest stages), product = the total
    top = {k.split(" | ")[0]: v for k, v in figures.items() if k.endswith("effect on cac %")}
    assert set(top) == {"CPC", "CLICKS per NEW"}
    assert abs((1 + top["CPC"] / 100) * (1 + top["CLICKS per NEW"] / 100) - 1.055) < 0.001
    assert top["CLICKS per NEW"] > 15 and top["CPC"] < -9
    # CPC split exactly by the other named rates: CPM up, CTR up (lowers CPC)
    sub = {k.split(" | ")[0]: v for k, v in figures.items() if k.endswith("effect on cpc %")}
    assert abs(sub["CPM"] - 6.1) < 0.2 and sub["CTR"] < -14
    assert "CPC itself = CPM ÷ CTR" in text


async def test_a_why_question_diagnoses_the_measure_the_question_names() -> None:
    """'ROAS' read as two slots (gross, net): the catalogue's reading of the question picks the outcome."""
    from seleric_swarm.agent.plan import MetricSlot, PlanSlots, plan_from_slots

    slots = PlanSlots(
        shape="why_single_metric",
        metrics=[MetricSlot(words="ROAS (gross)", metric_id="gross_roas"), MetricSlot(words="ROAS (net)", metric_id="net_roas")],
    )
    q = "Why did ROAS rise compared with last week?"

    async def resolver(texts: list[str]) -> dict[str, str | None]:
        table = {"ROAS (gross)": "gross_roas", "ROAS (net)": "net_roas", q: "net_roas"}
        return {t: table.get(t) for t in texts}

    out = await plan_from_slots(
        slots, catalogue=CatalogueSnapshot(), resolver=resolver, question=q,
        windows=[(date(2026, 10, 5), date(2026, 10, 8))], as_of=AS_OF,
    )
    assert out.plan is not None
    assert out.plan.steps[0].metric_ids == ["net_roas"]


def test_a_change_across_zero_between_shown_figures_is_backed() -> None:
    from seleric_swarm.agent.validation import _derived_from_shown

    assert _derived_from_shown(216.3, 0.05, True, [-8729.0, 10148.0], [])
    assert not _derived_from_shown(57.0, 0.05, True, [-8729.0, 10148.0], [])


async def test_a_composition_plan_runs_the_bridge_from_the_earlier_period(defs: None) -> None:
    """'On 10-02 we recorded a loss … versus 10-01 … reconcile': the planner runs break_down_metric itself, with
    the change running from 10-01 to 10-02 whichever is named first."""
    from seleric_swarm.agent.executor import execute_plan
    from seleric_swarm.agent.plan import MissionPlan, PlanStep

    mcp = _Mcp({
        "2026-10-02": {"np": -20.0, "cm": 80.0, "ns": 100.0, "cogs": 20.0, "ad": 100.0},
        "2026-10-01": {"np": 10.0, "cm": 90.0, "ns": 110.0, "cogs": 20.0, "ad": 80.0},
    })
    plan = MissionPlan(shape="composition", steps=[PlanStep(tool="break_down_metric", metric_ids=["np"], period="x", purpose="bridge the change")])
    out = await execute_plan(plan, _deps(mcp), windows=[(date(2026, 10, 2), date(2026, 10, 2)),
                                                       (date(2026, 10, 1), date(2026, 10, 1))], as_of=AS_OF)
    assert out is not None and "change -30.00" in out.text and out.finding_id


async def test_a_composition_total_follows_the_basis_the_question_names() -> None:
    """Golden Q22 (2026-10-09): 'net profit waterfall for September' — the understanding worded the slot 'P&L net
    profit', the resolver gave the event-date twin, and the waterfall reconciled −1.53M instead of −170k. The
    question's own reading (order date, the default) wins over a twin the user never asked for."""
    from seleric_swarm.agent.plan import MetricSlot, PlanSlots, plan_from_slots
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta

    cat = CatalogueSnapshot(metrics=(
        CatalogueMetricMeta(id="net_profit", view="order_pnl", raw={"date_basis": "order", "date_twin": "pnl_net_profit"}),
        CatalogueMetricMeta(id="pnl_net_profit", view="pnl", raw={"date_basis": "finance", "date_twin": "net_profit"}),
    ))
    q = "give me a net profit breakdown, waterfall for September 2026"

    async def resolver(texts: list[str]) -> dict[str, str | None]:
        return {t: ("pnl_net_profit" if "P&L" in t else "net_profit") for t in texts}

    slots = PlanSlots(shape="composition", metrics=[MetricSlot(words="P&L net profit", metric_id="pnl_net_profit")])
    out = await plan_from_slots(slots, catalogue=cat, resolver=resolver, question=q,
                                windows=[(date(2026, 9, 1), date(2026, 9, 30))], as_of=AS_OF)
    assert out.plan is not None and out.plan.steps[0].metric_ids == ["net_profit"]


def test_a_change_or_share_of_one_metrics_own_values_is_backed_and_nothing_else_is() -> None:
    from seleric_swarm.agent.validation import _backed, _mission_values
    from tests.unit.test_failures_20261008 import _deps as fdeps
    from tests.unit.test_failures_20261008 import _row

    store = InMemoryArtifactStore()
    _row(store, "a", 300.0)
    _row(store, "b", 100.0)
    pool = _mission_values(fdeps(store))
    assert _backed(75.0, 0.05, True, pool)        # a's share of the a+b total
    assert _backed(25.0, 0.05, True, pool)
    assert not _backed(41.0, 0.05, True, pool)    # an invented percent


async def test_a_date_basis_only_the_slot_names_is_not_the_users() -> None:
    """The understanding wrote 'P&L net profit' (a catalogue label) for a question that names no date basis: the
    order-date twin is used. A question that itself says 'on the P&L' keeps the Finance metric."""
    from seleric_swarm.agent.runner import _ConceptResolver
    from seleric_swarm.agent.scope import RequiredScope
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta

    cat = CatalogueSnapshot(metrics=(
        CatalogueMetricMeta(id="net_profit", view="order_pnl", raw={"date_basis": "order", "date_twin": "pnl_net_profit"}),
        CatalogueMetricMeta(id="pnl_net_profit", view="pnl", raw={"date_basis": "finance", "date_twin": "net_profit"}),
    ))

    class Gateway:
        async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> dict[str, Any]:
            if capability.endswith("resolve_concept"):
                return {"kind": "resolved_concept", "metric_id": "pnl_net_profit", "filter": {}}
            return {"status": "ok", "axes": {"date": "finance"} if "P&L" in arguments["text"] else {}}

    plain = _ConceptResolver(Gateway(), RequiredScope(), cat)
    assert (await plain(["P&L net profit"]))["P&L net profit"] == "net_profit"
    asked = _ConceptResolver(Gateway(), RequiredScope(question_axes=(("date", "finance"),)), cat)
    assert (await asked(["P&L net profit"]))["P&L net profit"] == "pnl_net_profit"


async def test_a_driver_the_question_names_never_takes_the_outcomes_place() -> None:
    """Regression Q24: 'Why did CAC increase … checkout conversion …' — the whole-question reading named
    checkout rate; CAC stays the outcome because checkout rate is a different measure, not a variant of it."""
    from seleric_swarm.agent.plan import MetricSlot, PlanSlots, plan_from_slots

    q = "Why did CAC increase yesterday? Check whether the change came from CPM or checkout conversion."
    slots = PlanSlots(shape="why_single_metric", metrics=[
        MetricSlot(words="customer acquisition cost", metric_id="cac"),
        MetricSlot(words="checkout conversion", metric_id="checkout_rate"),
    ])

    async def resolver(texts: list[str]) -> dict[str, str | None]:
        table = {"customer acquisition cost": "cac", "checkout conversion": "checkout_rate", q: "checkout_rate"}
        return {t: table.get(t) for t in texts}

    out = await plan_from_slots(slots, catalogue=CatalogueSnapshot(), resolver=resolver, question=q,
                                windows=[(date(2026, 10, 8), date(2026, 10, 8))], as_of=AS_OF)
    assert out.plan is not None and out.plan.steps[0].metric_ids == ["cac"]


async def test_a_breakdowns_evidence_records_the_filters_it_was_fetched_with(defs: None) -> None:
    mcp = _Mcp({"2026-10-01": {"np": -20.0, "cm": 80.0, "ns": 100.0, "cogs": 20.0, "ad": 100.0}})
    deps = _deps(mcp)
    res = await composition.break_down_metric(
        _Ctx(deps), "np", period_start=datetime(2026, 10, 1, tzinfo=UTC), filters={"finance_channel": "meta"}
    )
    ev = deps.artifact_store.get(res.provenance.evidence_ids[0])
    assert {"dimension": "finance_channel", "operator": "equals", "values": ["meta"]} in ev.provenance.source_metadata["filters_applied"]


async def test_a_threshold_on_another_views_metric_is_refused_with_the_views_own_metrics() -> None:
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta
    from seleric_swarm.toolsets import semantic

    cat = CatalogueSnapshot(metrics=(
        CatalogueMetricMeta(id="net_roas", view="order_pnl"),
        CatalogueMetricMeta(id="order_pnl_ad_spend", view="order_pnl"),
        CatalogueMetricMeta(id="ad_spend", view="paid_media"),
    ))
    import dataclasses

    deps = dataclasses.replace(_deps(_Mcp({})), catalogue=cat)
    res = semantic._cross_view_metric_filter(
        _Ctx(deps), "net_roas", [semantic.MetricFilter(dimension="ad_spend", operator="gt", values=["0"])]
    )
    assert res is not None and not res.success and res.retryable and "order_pnl_ad_spend" in res.summary
    same = semantic._cross_view_metric_filter(
        _Ctx(deps), "net_roas", [semantic.MetricFilter(dimension="order_pnl_ad_spend", operator="gt", values=["0"])]
    )
    assert same is None


async def test_label_words_the_user_never_wrote_are_dropped_before_resolving() -> None:
    """The understanding copies catalogue labels into slots ('P&L net profit', 'Product CAC (allocated)'); the
    words of the matched label the user did not write name a variant they never asked for."""
    from seleric_swarm.agent.runner import _ConceptResolver
    from seleric_swarm.agent.scope import RequiredScope
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta

    cat = CatalogueSnapshot(metrics=(
        CatalogueMetricMeta(id="cac", label="CAC", view="unit_economics"),
        CatalogueMetricMeta(id="product_cac", label="Product CAC (allocated)", view="product_pnl"),
    ))

    class Gateway:
        async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> dict[str, Any]:
            if capability.endswith("resolve_concept"):
                mid = "product_cac" if "product" in arguments["text"].lower() else "cac"
                return {"kind": "resolved_concept", "metric_id": mid, "filter": {}}
            return {"status": "ok", "axes": {}}

    # the runner passes the question without the words read as dimensions ("product mix" -> product_type)
    from seleric_swarm.agent.runner import _outside_measures

    q = _outside_measures("Why did CAC increase yesterday? Was it product mix?", ["product type"])
    r = _ConceptResolver(Gateway(), RequiredScope(), cat, q)
    assert (await r(["Product CAC (allocated)"]))["Product CAC (allocated)"] == "cac"
    asked = _ConceptResolver(Gateway(), RequiredScope(), cat, "What was product CAC by product type?")
    assert (await asked(["product CAC"]))["product CAC"] == "product_cac"


def test_a_quotient_of_two_metrics_of_one_slice_and_period_is_backed() -> None:
    from seleric_swarm.agent.validation import _backed, _mission_values
    from tests.unit.test_failures_20261008 import _deps as fdeps
    from tests.unit.test_failures_20261008 import _row

    store = InMemoryArtifactStore()
    _row(store, "meta", 69847.63, metric="ad_spend")
    _row(store, "meta", 40.0, metric="new_customers")
    pool = _mission_values(fdeps(store))
    assert _backed(1746.19, 0.005, False, pool)      # spend per new customer
    assert not _backed(1912.40, 0.005, False, pool)


def test_a_diagnosis_filter_moves_to_the_metrics_own_family_member() -> None:
    import dataclasses

    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta

    cat = CatalogueSnapshot(
        metrics=(CatalogueMetricMeta(id="sessions", view="web", supported_dimensions=["acquisition_platform"]),),
        dimension_families=(("finance_channel", "platform", 1), ("acquisition_platform", "platform", 3)),
    )
    deps = dataclasses.replace(_deps(_Mcp({})), catalogue=cat)
    out, dropped = diagnosis._filters_for(_Ctx(deps), "sessions", {"finance_channel": "meta"})
    assert out == [{"dimension": "acquisition_platform", "operator": "equals", "values": ["meta"]}] and not dropped


async def test_metric_definitions_are_fetched_in_batches_the_gateway_accepts() -> None:
    """The gateway refuses more than 10 ids per call with a bare 'error'; 11 ids came back as 0 definitions."""
    from seleric_swarm.toolsets import semantic

    calls: list[list[str]] = []

    class Gateway:
        async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> dict[str, Any]:
            ids = arguments["metric_ids"]
            calls.append(ids)
            if len(ids) > 10:
                return {"error": "Too many metric ids"}
            return {"metrics": {m: {"id": m} for m in ids if m != "nope"}, "errors": {"nope": "unknown"} if "nope" in ids else {}}

    ids = [f"m{i}" for i in range(11)] + ["nope"]
    res = await semantic.get_metric_definitions(_Ctx(_deps(Gateway())), ids)
    assert res.success and len(res.provenance.source_metadata["definitions"]) == 11
    assert all(len(c) <= 10 for c in calls) and "unknown metric id 'nope'" in res.warnings


async def test_a_rate_broken_down_by_entity_is_planned_with_its_base() -> None:
    from seleric_swarm.agent.plan import MetricSlot, PlanSlots, plan_from_slots
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta

    cat = CatalogueSnapshot(metrics=(
        CatalogueMetricMeta(id="product_return_rate", view="product_pnl",
                            raw={"aggregation": "ratio", "volume_metric": "product_order_orders"}),
        CatalogueMetricMeta(id="product_order_orders", view="product_pnl", raw={"aggregation": "additive"}),
    ))
    slots = PlanSlots(shape="breakdown", breakdown_dimensions=["product_title"],
                      metrics=[MetricSlot(words="return rate", metric_id="product_return_rate")])
    out = await plan_from_slots(slots, catalogue=cat, windows=[(date(2026, 9, 1), date(2026, 9, 30))], as_of=AS_OF)
    assert out.plan is not None
    assert "product_order_orders" in out.plan.steps[0].metric_ids


async def test_a_breakdown_sliced_by_a_product_uses_the_totals_product_twin(monkeypatch: pytest.MonkeyPatch) -> None:
    """'profit waterfall for <product>': the order-date P&L cannot be sliced by product, so the product P&L twin
    (its own verified composition) is broken down instead of the brand total with the filter dropped."""
    import dataclasses

    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta

    defs = {
        "np": {"display_name": "Net profit", "formula": {"composition": [{"metric": "cm", "sign": 1}, {"metric": "ad", "sign": -1}]}},
        "pnp": {"display_name": "Product net profit", "formula": {"composition": [{"metric": "pcm", "sign": 1}, {"metric": "pad", "sign": -1}]}},
        "cm": {}, "ad": {}, "pcm": {}, "pad": {},
    }

    async def all_definitions(ctx: Any) -> dict[str, Any]:
        return defs

    monkeypatch.setattr(diagnosis, "_all_definitions", all_definitions)
    cat = CatalogueSnapshot(metrics=(
        CatalogueMetricMeta(id="np", view="order_pnl", supported_dimensions=["finance_channel"], raw={"grain_twins": ["pnp"]}),
        *(CatalogueMetricMeta(id=m, view="product_pnl", supported_dimensions=["product_title"]) for m in ("pnp", "pcm", "pad")),
        *(CatalogueMetricMeta(id=m, view="order_pnl", supported_dimensions=["finance_channel"]) for m in ("cm", "ad")),
    ))
    mcp = _Mcp({"2026-09-01": {"pnp": 10.0, "pcm": 30.0, "pad": 20.0}})
    deps = dataclasses.replace(_deps(mcp), catalogue=cat)
    res = await composition.break_down_metric(
        _Ctx(deps), "np", period_start=datetime(2026, 9, 1, tzinfo=UTC), period_end=datetime(2026, 9, 30, tzinfo=UTC),
        filters={"product_title": "Boots"},
    )
    assert res.success and "grain twin pnp" in res.summary and "Reconciled" in res.summary
    assert mcp.calls[0]["measures"][0] == "pnp"
    assert {"dimension": "product_title", "operator": "equals", "values": ["Boots"]} in mcp.calls[0]["filters"]


async def test_companions_of_a_breakdown_are_fetched_for_the_anchors_entities() -> None:
    """'return rate by product': the rate and its base were fetched as two independent top lists and described
    different products. The additive base ranks; the rate is fetched for exactly those products."""
    import dataclasses

    from seleric_swarm.agent.executor import execute_plan
    from seleric_swarm.agent.plan import MissionPlan, PlanStep
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta

    cat = CatalogueSnapshot(metrics=(
        CatalogueMetricMeta(id="rate", view="v", supported_dimensions=["product_title"], raw={"aggregation": "ratio"}),
        CatalogueMetricMeta(id="orders", view="v", supported_dimensions=["product_title"], raw={"aggregation": "additive"}),
    ))

    class Gateway:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> dict[str, Any]:
            self.calls.append(arguments)
            m = arguments["measures"][0]
            rows = [{"product_title": "A", m: 10.0}, {"product_title": "B", m: 5.0}]
            return {"rows": rows, "provenance": {}}

    gw = Gateway()
    deps = dataclasses.replace(_deps(gw), catalogue=cat)  # type: ignore[arg-type]
    plan = MissionPlan(shape="breakdown", steps=[PlanStep(tool="query_metrics", metric_ids=["rate", "orders"],
                                                          dimensions=["product_title"], period="x", purpose="rate by product")])
    await execute_plan(plan, deps, windows=[(date(2026, 9, 1), date(2026, 9, 30))], as_of=AS_OF)
    rate_calls = [c for c in gw.calls if c["measures"] == ["rate"]]
    assert gw.calls[0]["measures"] == ["orders"]
    assert rate_calls and any(
        f.get("dimension") == "product_title" and set(f.get("values") or []) == {"A", "B"} for f in rate_calls[0].get("filters") or []
    )


async def test_a_breakdown_split_by_a_dimension_reconciles_per_segment(defs: None) -> None:
    class Split(_Mcp):
        async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> dict[str, Any]:
            self.calls.append(arguments)
            if arguments.get("dimensions"):
                return {"rows": [
                    {"finance_channel": "meta", "np": -30.0, "cm": 50.0, "ns": 60.0, "cogs": 10.0, "ad": 80.0},
                    {"finance_channel": "organic", "np": 10.0, "cm": 30.0, "ns": 40.0, "cogs": 10.0, "ad": 20.0},
                ], "provenance": {}}
            return {"rows": [{"np": -20.0, "cm": 80.0, "ns": 100.0, "cogs": 20.0, "ad": 100.0}], "provenance": {}}

    mcp = Split({})
    res = await composition.break_down_metric(
        _Ctx(_deps(mcp)), "np", period_start=datetime(2026, 9, 1, tzinfo=UTC), by="finance_channel"
    )
    assert res.success
    assert "By finance_channel" in res.summary and "every segment's lines reconcile" in res.summary
    assert "| meta |" in res.summary and "| organic |" in res.summary


async def test_a_line_the_semantic_layer_splits_is_opened_into_its_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    defs = {
        "ns": {"display_name": "Net sales", "formula": {"composition": [
            {"metric": "gs", "sign": 1}, {"metric": "disc", "sign": -1}, {"metric": "ded", "sign": -1}]}},
        "gs": {"display_name": "Gross sales"}, "disc": {"display_name": "Discounts"},
        "ded": {"display_name": "Deductions", "split_by": "refund_class"},
    }

    async def all_definitions(ctx: Any) -> dict[str, Any]:
        return defs

    monkeypatch.setattr(diagnosis, "_all_definitions", all_definitions)

    class Split(_Mcp):
        async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> dict[str, Any]:
            self.calls.append(arguments)
            if arguments.get("dimensions") == ["refund_class"]:
                return {"rows": [{"refund_class": "RETURN", "ded": 12.0}, {"refund_class": "CANCELLATION", "ded": 8.0}],
                        "provenance": {}}
            return {"rows": [{"ns": 70.0, "gs": 100.0, "disc": 10.0, "ded": 20.0}], "provenance": {}}

    res = await composition.break_down_metric(_Ctx(_deps(Split({}))), "ns", period_start=datetime(2026, 9, 1, tzinfo=UTC))
    assert "Inside Deductions (20.00) by refund_class: RETURN 12.00; CANCELLATION 8.00." in res.summary


async def test_a_why_plan_carries_the_named_measures_as_drivers() -> None:
    from seleric_swarm.agent.plan import MetricSlot, PlanSlots, plan_from_slots

    slots = PlanSlots(shape="why_single_metric", metrics=[
        MetricSlot(words="CAC", metric_id="cac"), MetricSlot(words="CPM", metric_id="cpm"),
        MetricSlot(words="CTR", metric_id="ctr"),
    ])
    async def resolver(texts: list[str]) -> dict[str, str | None]:
        return {t: t.lower() for t in texts}

    out = await plan_from_slots(slots, catalogue=CatalogueSnapshot(), resolver=resolver,
                                windows=[(date(2026, 10, 8), date(2026, 10, 8))], as_of=AS_OF)
    assert out.plan is not None
    step = out.plan.steps[0]
    assert step.metric_ids == ["cac"] and step.drivers == ["cpm", "ctr"]


def test_per_day_averages_of_fetched_totals_and_their_change_are_backed() -> None:
    from seleric_swarm.agent.artifacts import EvidenceArtifact
    from seleric_swarm.agent.validation import _backed, _mission_values
    from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
    from tests.unit.test_failures_20261008 import _deps as fdeps

    store = InMemoryArtifactStore()
    for start, end, value in (("2026-10-01", "2026-10-06", 600.0), ("2026-10-07", "2026-10-09", 480.0)):
        ev = EvidenceArtifact(metric_id="orders", dimensions={}, grain="none", as_of=AS_OF,
                              period_start=datetime.fromisoformat(start + "T00:00:00+05:30"),
                              period_end=datetime.fromisoformat(end + "T00:00:00+05:30"), value=value,
                              source_query={"measure": "orders"})
        store.put(Artifact(workspace_id="w", artifact_type="evidence", payload=ev.model_dump(mode="json"),
                           classification="factual", evidence_ids=["raw"], provenance=ArtifactProvenance(), mission_id="m1"))
    pool = _mission_values(fdeps(store))
    assert _backed(100.0, 0.005, False, pool) and _backed(160.0, 0.005, False, pool)   # per day
    assert _backed(60.0, 0.05, True, pool)                                             # +60% per day


def test_drilldowns_of_different_parent_queries_never_contradict() -> None:
    from seleric_swarm.agent.artifacts import EvidenceArtifact
    from seleric_swarm.agent.validation.signals import check_contradiction
    from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance

    arts = []
    for parent, value in (("q_product", 353010.0), ("q_all", 2438388.0)):
        ev = EvidenceArtifact(metric_id="product_net_revenue", dimensions={"finance_channel": "meta"}, grain="none",
                              as_of=AS_OF, period_start=AS_OF, period_end=AS_OF, value=value,
                              source_query={"parent_query_id": parent, "target_dimensions": ["finance_channel"]})
        arts.append(Artifact(workspace_id="w", artifact_type="evidence", payload=ev.model_dump(mode="json"),
                             classification="factual", evidence_ids=["raw"], provenance=ArtifactProvenance()))
    assert not check_contradiction(arts).challenges


def test_a_subtotal_of_the_rows_its_sentence_names_is_not_a_mismatched_total() -> None:
    from seleric_swarm.agent.validation.answer_audit import total_mismatch

    rows = "\n".join(f"| Campaign {i} | {1000 + i * 37.5:,.2f} |" for i in range(40))
    table = "| Source | Net revenue |\n| --- | ---: |\n| whatsapp | 4,200.50 |\n| direct | 8,506.28 |\n" + rows
    text = table + "\n\nWhatsApp and direct together total 12,706.78."
    assert total_mismatch(text) is None
    assert total_mismatch(table + "\n\nThe total is 99,999.99.") is not None


def test_evidence_period_stamps_are_always_timezone_aware() -> None:
    from datetime import timedelta, timezone

    from seleric_swarm.agent.artifacts import EvidenceArtifact

    ist = timezone(timedelta(hours=5, minutes=30))
    naive = EvidenceArtifact(metric_id="m", grain="none", as_of=datetime(2026, 10, 9, tzinfo=ist),
                             period_start=datetime(2026, 10, 1), period_end=datetime(2026, 10, 7), value=1.0,
                             source_query={})
    aware = EvidenceArtifact(metric_id="m", grain="none", as_of=datetime(2026, 10, 9, tzinfo=ist),
                             period_start=datetime(2026, 10, 2, tzinfo=ist), period_end=datetime(2026, 10, 7, tzinfo=ist),
                             value=1.0, source_query={})
    assert naive.period_start.tzinfo is not None
    assert sorted([aware.period_start, naive.period_start])[0] == naive.period_start   # comparable


def test_a_month_named_without_a_year_is_its_latest_occurrence() -> None:
    from seleric_swarm.services.time_range import window_from_query

    as_of = "2026-10-10T08:00:00+05:30"
    w = window_from_query("AOV and COD orders by product in September?", "Asia/Kolkata", as_of)
    assert (w.kind, w.start, w.end) == ("absolute", "2026-09-01", "2026-09-30")
    w = window_from_query("net sales in November", "Asia/Kolkata", as_of)
    assert (w.start, w.end) == ("2025-11-01", "2025-11-30")
    w = window_from_query("orders so far in October", "Asia/Kolkata", as_of)
    assert (w.start, w.end) == ("2026-10-01", "2026-10-09")
    w = window_from_query("compare September with August", "Asia/Kolkata", as_of)
    assert (w.kind, w.start, w.start_b) == ("comparison", "2026-09-01", "2026-08-01")
    assert window_from_query("what may have caused the drop?", "Asia/Kolkata", as_of) is None or \
        window_from_query("what may have caused the drop?", "Asia/Kolkata", as_of).start != "2026-05-01"


async def test_each_entity_kind_the_user_lists_is_ranked_on_its_own_carried_slice() -> None:
    from seleric_swarm.agent.plan import PlanSlots, MetricSlot, plan_from_slots
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta

    cat = CatalogueSnapshot(
        metrics=(CatalogueMetricMeta(id="net_profit", view="order_pnl", supported_dimensions=["campaign_name", "finance_channel", "product_title"]),),
        dimensions=("campaign_name", "finance_channel", "channel", "product_title"),
    )
    slots = PlanSlots(shape="entity_comparison", metrics=[MetricSlot(words="net profit", metric_id="net_profit")],
                      rank_by=MetricSlot(words="net profit", metric_id="net_profit"), rank_order="asc",
                      entity_dimension="campaign_name", breakdown_dimensions=["campaign_name", "product_title", "channel"])

    async def resolver(texts: list[str]) -> dict[str, str | None]:
        return {t: "net_profit" for t in texts}

    out = await plan_from_slots(slots, catalogue=cat, resolver=resolver,
                                windows=[(date(2026, 10, 2), date(2026, 10, 2))], as_of=AS_OF)
    ranked = [s.dimensions for s in out.plan.steps if s.ranking]
    assert ranked == [["campaign_name"], ["product_title"], ["finance_channel"]], ranked
    assert all(len(s.dimensions) <= 1 for s in out.plan.steps)


def test_an_entity_label_covers_a_breakdown_by_its_key() -> None:
    from seleric_swarm.agent.scope import scope_from_dimensions

    scope = scope_from_dimensions(["product_id"], alias_index={}, dimension_ids={"product_id", "product_title"},
                                  stable_keys=(("product_title", "product_id"),))
    assert scope.breakdowns == frozenset({frozenset({"product_id", "product_title"})})


def test_the_models_own_queries_carry_the_one_named_value_unless_weighed_against_the_whole() -> None:
    import dataclasses
    from types import SimpleNamespace

    from seleric_swarm.agent.scope import RequiredScope, ValueFilter
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta
    from seleric_swarm.toolsets.semantic import _scoped_to_named_values

    cat = CatalogueSnapshot(metrics=(CatalogueMetricMeta(id="impressions", view="paid_media",
                                                         supported_dimensions=["ad_platform", "campaign_name"]),),
                            dimensions=("ad_platform", "campaign_name"))
    meta = ValueFilter(term="meta", dimensions=frozenset({"ad_platform"}), values=("meta",))
    scope = RequiredScope(value_filters=(meta,))
    ctx = SimpleNamespace(deps=SimpleNamespace(required_scope=scope, catalogue=cat))
    asked = {"campaign_name": ["A", "B"]}
    assert _scoped_to_named_values(ctx, "impressions", asked) == {"campaign_name": ["A", "B"], "ad_platform": "meta"}
    ctx.deps.required_scope = dataclasses.replace(scope, values_weighed_against_whole=True)
    assert _scoped_to_named_values(ctx, "impressions", asked) == asked


async def test_named_drivers_are_diagnosed_by_the_executor_with_them(monkeypatch) -> None:
    from types import SimpleNamespace

    from seleric_swarm.agent import executor
    from seleric_swarm.agent.plan import MissionPlan, PlanStep
    from seleric_swarm.toolsets import diagnosis

    seen: dict = {}

    async def fake(ctx, metric_id, **kwargs):
        seen.update(metric_id=metric_id, **kwargs)
        return SimpleNamespace(success=True, summary="ANSWER SKELETON", artifact_ids=["e1", "f1"],
                               provenance=SimpleNamespace(source_metadata={"finding_id": "f1"}))

    monkeypatch.setattr(diagnosis, "diagnose_metric_change", fake)
    plan = MissionPlan(shape="why_single_metric", steps=[PlanStep(tool="diagnose_metric_change", metric_ids=["cac"],
                                                                  drivers=["cpm", "ctr", "cpc"], purpose="diagnose")])
    from tests.unit.test_failures_20261008 import _deps as fdeps
    deps = fdeps(InMemoryArtifactStore())
    out = await executor.execute_plan(plan, deps, windows=[(date(2026, 10, 9), date(2026, 10, 9))], as_of=AS_OF)
    assert out is not None and seen["drivers"] == ["cpm", "ctr", "cpc"] and seen["metric_id"] == "cac"
    assert seen["event_start"].date() == date(2026, 10, 9) and out.evidence_ids == ["e1"]
    plain = MissionPlan(shape="why_single_metric", steps=[PlanStep(tool="diagnose_metric_change", metric_ids=["cac"],
                                                                   purpose="diagnose")])
    assert await executor.execute_plan(plain, deps, windows=[(date(2026, 10, 9), date(2026, 10, 9))], as_of=AS_OF) is None
