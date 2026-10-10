"""Live thread_066b9cd1 (2026-10-10): a campaign drill-down answered from account totals, and why-questions that
named no driver while conversion had halved."""

from __future__ import annotations

import random
from datetime import date, timedelta

from seleric_swarm.agent.scope import ValueFilter, promote_verbatim_values, value_filters_from_resolution
from seleric_swarm.toolsets import diagnosis

_QUESTION = (
    "TH-383-SUSPENDER-26SEP-ADV+ , drill down into this campaign and why did it not perform yesterdays as compare "
    "to the other days"
)


def _dim(dimension: str, view: str, *values: tuple[str, float, str]) -> dict:
    return {"dimension": dimension, "view": view,
            "values": [{"value": v, "volume": vol, "match": m} for v, vol, m in values]}


# The gateway's reply, trimmed: the name split into word terms, the whole name only a token hit.
_RESOLUTION = {"status": "ok", "terms": [
    {"term": "adv", "catalogue_vocabulary": False, "best_match": "exact", "dimensions": [
        _dim("entity_name", "paid_media_changes", ("Adv+", 0.0, "exact"), ("TH-053-COAT-3JULY-ADV+", 1.0, "token")),
        _dim("adset_name", "paid_media_changes", ("Adv+", 0.0, "exact")),
    ]},
    {"term": "other", "catalogue_vocabulary": False, "best_match": "exact", "dimensions": [
        _dim("channel", "orders", ("other", 120.0, "exact")),
    ]},
    {"term": "campaign", "catalogue_vocabulary": False, "best_match": "exact", "dimensions": [
        _dim("entity_type", "paid_media_changes", ("CAMPAIGN", 166.0, "exact")),
    ]},
    {"term": "suspender 26sep adv", "catalogue_vocabulary": False, "best_match": "token", "dimensions": [
        _dim("campaign_name", "payments", ("TH-383-SUSPENDER-26SEP-ADV+", 75318.9, "token"),
             ("TH-383-SUSPENDER", 9000.0, "token")),
        _dim("acquisition_campaign", "customers", ("TH-383-SUSPENDER-26SEP-ADV+", 24.0, "token")),
    ]},
    {"term": "suspender", "catalogue_vocabulary": False, "best_match": "token", "dimensions": [
        _dim("ad_name", "paid_media", ("TH-383-SUSPENDER-UGC", 500.0, "token")),
    ]},
    {"term": "26sep", "catalogue_vocabulary": False, "best_match": "token", "dimensions": [
        _dim("campaign_name", "payments", ("TH-383-SUSPENDER-26SEP-ADV+", 75318.9, "token")),
    ]},
]}


def test_a_name_written_out_whole_is_an_exact_value_and_its_fragments_go():
    promoted = promote_verbatim_values(_RESOLUTION, _QUESTION)
    assert [t["term"] for t in promoted["terms"]] == ["th-383-suspender-26sep-adv+", "other", "campaign"]
    assert value_filters_from_resolution(promoted)[0] == ValueFilter(
        term="th-383-suspender-26sep-adv+",
        dimensions=frozenset({"campaign_name", "acquisition_campaign"}),
        values=("TH-383-SUSPENDER-26SEP-ADV+",),
    )
    # "Adv+" (zero rows) no longer becomes a filter
    assert not any("Adv+" in vf.values for vf in value_filters_from_resolution(promoted))


def test_a_shorter_name_inside_a_longer_one_is_not_a_second_value():
    promoted = promote_verbatim_values(_RESOLUTION, _QUESTION)
    values = [v for vf in value_filters_from_resolution(promoted) for v in vf.values]
    assert "TH-383-SUSPENDER" not in values


def test_a_name_not_written_out_whole_stays_a_hint():
    question = "why did suspender 26sep campaigns drop"
    assert promote_verbatim_values(_RESOLUTION, question) == _RESOLUTION


def test_a_fragment_the_question_also_uses_on_its_own_is_kept():
    question = "TH-383-SUSPENDER-26SEP-ADV+ against the other suspender ads"
    terms = [t["term"] for t in promote_verbatim_values(_RESOLUTION, question)["terms"]]
    assert "suspender" in terms and "26sep" not in terms


def _sales_lineage() -> dict:
    from seleric_swarm.causal import diagnosis as engine

    def m(i: str, agg: str, deps: tuple[str, ...] = ()) -> engine.MetricMeta:
        return engine.MetricMeta(i, aggregation=agg, depends_on=deps, label=i.upper())

    return {
        "sales": m("sales", "additive"), "orders": m("orders", "additive"), "sessions": m("sessions", "additive"),
        "clicks": m("clicks", "additive"), "impr": m("impr", "additive"),
        "cvr": m("cvr", "ratio", ("orders", "sessions")), "aov": m("aov", "ratio", ("sales", "orders")),
        "ctr": m("ctr", "ratio", ("clicks", "impr")),
    }


def _sales_series() -> dict[str, dict[date, float]]:
    """2026-10-08 → 10-09 (ClickHouse): sessions 5,042 → 5,094, purchases 58 → 26, gross sales 121,848 → 70,852."""
    rng = random.Random(3)
    series: dict[str, dict[date, float]] = {k: {} for k in ("sales", "orders", "sessions", "clicks", "impr")}
    for i in range(37):
        d = date(2026, 9, 2) + timedelta(days=i)
        se = 4800 + rng.random() * 500
        od = se * (0.010 + rng.random() * 0.003)
        series["sessions"][d], series["orders"][d] = se, od
        series["sales"][d] = od * (1900 + rng.random() * 300)
        series["impr"][d] = 190000 + rng.random() * 20000
        series["clicks"][d] = series["impr"][d] * 0.027
    for d, (se, od, sa) in {date(2026, 10, 8): (5042, 58, 121848.0), date(2026, 10, 9): (5094, 26, 70851.77)}.items():
        series["sessions"][d], series["orders"][d], series["sales"][d] = se, od, sa
        series["impr"][d], series["clicks"][d] = 200000, 5400
    series["cvr"] = {d: series["orders"][d] / series["sessions"][d] for d in series["sessions"]}
    series["aov"] = {d: series["sales"][d] / series["orders"][d] for d in series["sessions"]}
    series["ctr"] = {d: series["clicks"][d] / series["impr"][d] for d in series["impr"]}
    return series


def test_a_total_splits_exactly_through_the_rates_that_rebuild_it():
    series = _sales_series()
    history = sorted(d for d in series["sales"] if d < date(2026, 10, 9))
    text, figures = diagnosis._ratio_split(
        "sales", ["cvr", "aov", "ctr"], _sales_lineage(), series, history, [date(2026, 10, 9)], [date(2026, 10, 8)]
    )
    assert "EXACT SPLIT" in text
    effects = {k.split(" | ")[0]: v for k, v in figures.items() if k.endswith("effect on sales %")}
    assert set(effects) == {"CVR", "AOV", "SESSIONS"}
    # conversion halved on flat traffic: the drop is conversion's, not traffic's
    assert effects["CVR"] < -50 and abs(effects["SESSIONS"]) < 2
    product = 1.0
    for v in effects.values():
        product *= 1 + v / 100
    assert abs(product - 70851.77 / 121848.0) < 1e-6


def test_rates_that_do_not_rebuild_a_total_give_no_split():
    series = _sales_series()
    history = sorted(d for d in series["sales"] if d < date(2026, 10, 9))
    text, figures = diagnosis._ratio_split(
        "sales", ["ctr"], _sales_lineage(), series, history, [date(2026, 10, 9)], [date(2026, 10, 8)]
    )
    assert (text, figures) == ("", {})


def _campaign_catalogue():
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot

    def m(i: str, agg: str) -> CatalogueMetricMeta:
        return CatalogueMetricMeta(id=i, label=i.replace("_", " ").title(), supported_dimensions=["campaign_name"],
                                   raw={"aggregation": agg})

    return CatalogueSnapshot(
        metrics=(m("net_sales", "additive"), m("net_roas", "ratio"), m("ad_spend", "additive"),
                 m("ctr", "ratio"), m("orders", "additive")),
        dimension_families=(("campaign_name", "campaign", 0), ("acquisition_campaign", "campaign", 1)),
    )


def _comparison_reading(**over):
    from seleric_swarm.agent.plan import MetricSlot
    from seleric_swarm.agent.understand import Understanding

    base = dict(
        shape="entity_comparison", entity_dimension="acquisition_campaign", rank_by=None,
        metrics=[MetricSlot(words="ad spend", metric_id="ad_spend"), MetricSlot(words="CTR", metric_id="ctr"),
                 MetricSlot(words="orders", metric_id="orders")],
        breakdown_dimensions=[], kind="analysis", names_period=True, direction="decrease",
    )
    return Understanding(**{**base, **over})


_NAMED = ValueFilter(term="th-383-suspender-26sep-adv+", dimensions=frozenset({"campaign_name", "acquisition_campaign"}),
                     values=("TH-383-SUSPENDER-26SEP-ADV+",))


def test_one_named_campaign_against_its_own_days_is_diagnosed_on_what_it_returned():
    from seleric_swarm.agent.understand import diagnose_one_named_entity

    out = diagnose_one_named_entity(_comparison_reading(), [_NAMED], _campaign_catalogue(), ["net_sales", "net_roas"])
    assert out.shape == "why_single_metric"
    # the outcome is the first headline ratio the campaign carries; the measures read are its levers
    assert [s.metric_id for s in out.metrics] == ["net_roas", "ad_spend", "ctr", "orders"]


def test_the_measure_entities_are_judged_by_is_the_outcome():
    from seleric_swarm.agent.plan import MetricSlot
    from seleric_swarm.agent.understand import diagnose_one_named_entity

    reading = _comparison_reading(rank_by=MetricSlot(words="orders", metric_id="orders"))
    out = diagnose_one_named_entity(reading, [_NAMED], _campaign_catalogue(), ["net_roas"])
    assert [s.metric_id for s in out.metrics] == ["orders", "ad_spend", "ctr"] and out.rank_by is None


def test_several_named_campaigns_stay_a_comparison():
    from seleric_swarm.agent.understand import diagnose_one_named_entity

    two = ValueFilter(term="a", dimensions=frozenset({"campaign_name"}), values=("A-1", "B-2"))
    reading = _comparison_reading()
    assert diagnose_one_named_entity(reading, [two], _campaign_catalogue(), ["net_roas"]) is reading
    other = ValueFilter(term="meta", dimensions=frozenset({"ad_platform"}), values=("meta",))
    assert diagnose_one_named_entity(reading, [other], _campaign_catalogue(), ["net_roas"]) is reading


def test_a_lever_outside_its_own_usual_range_leads_the_drivers():
    """TH-383-SUSPENDER-26SEP-ADV+ 10-02..10-08 (ClickHouse): CPM 350, 286, 250, 297, 326, 356, 353; clicks
    191..1,079. A CPM of 520 yesterday is out of line for this campaign; clicks of 558 are not."""
    from seleric_swarm.causal import diagnosis as engine

    days = [date(2026, 10, 2) + timedelta(days=i) for i in range(7)]
    event = [date(2026, 10, 9)]
    series = {
        "cpm": dict(zip(days, [350.3, 286.1, 249.8, 296.8, 325.6, 355.6, 352.9], strict=True)) | {event[0]: 520.0},
        "clicks": dict(zip(days, [191, 143, 1079, 428, 572, 473, 513], strict=True)) | {event[0]: 558.0},
    }
    lineage = {"cpm": engine.MetricMeta("cpm", aggregation="ratio", label="CPM"),
               "clicks": engine.MetricMeta("clicks", aggregation="additive", label="Clicks")}
    report = engine.DiagnosisReport(outcome="net_roas", event_window=["2026-10-09"], verdict="no_unusual_change",
                                    headline="", event=None)
    text, figures = diagnosis._named_driver_lines(["clicks", "cpm"], series, event, days, lineage, report, days)
    assert text.index("CPM") < text.index("Clicks")
    assert "CPM" in text.split("OUTSIDE its own usual range")[0]
    assert "Clicks" in text and "within its own usual range" in text
    assert figures["cpm | z vs its own normal days"] > 2 > abs(figures["clicks | z vs its own normal days"])


def test_a_headline_ratio_the_reading_holds_is_the_outcome():
    from seleric_swarm.agent.plan import MetricSlot
    from seleric_swarm.agent.understand import diagnose_one_named_entity

    reading = _comparison_reading(metrics=[MetricSlot(words="ad spend", metric_id="ad_spend"),
                                           MetricSlot(words="net ROAS", metric_id="net_roas")])
    cat = _campaign_catalogue()
    out = diagnose_one_named_entity(reading, [_NAMED], cat, ["net_sales", "ctr", "net_roas"])
    assert [s.metric_id for s in out.metrics] == ["net_roas", "ad_spend"]


def test_the_outcome_is_placed_among_its_own_normal_days():
    from seleric_swarm.causal import diagnosis as engine

    days = [date(2026, 10, 2) + timedelta(days=i) for i in range(7)]
    roas = dict(zip(days, [0.0, 0.77, 0.39, 0.38, 0.90, 0.47, 0.71], strict=True)) | {date(2026, 10, 9): 0.65}
    lineage = {"net_roas": engine.MetricMeta("net_roas", aggregation="ratio", label="Net ROAS")}
    text = diagnosis._outcome_on_its_normal_days(
        "net_roas", {"net_roas": roas}, [date(2026, 10, 9)], days, days, lineage
    )
    assert "Net ROAS ranged" in text and "over 7 normal days" in text and "standing problem" in text


def test_the_count_of_an_outcomes_own_units_comes_from_its_grain():
    from seleric_swarm.causal import diagnosis as engine

    lineage = {
        "gross_sales": engine.MetricMeta("gross_sales", aggregation="additive", view="commerce", grain="order"),
        "orders": engine.MetricMeta("orders", aggregation="additive", view="commerce", grain="order"),
        "funnel_purchases": engine.MetricMeta("funnel_purchases", aggregation="additive", view="web_funnel",
                                              grain="brand_day"),
        "sessions": engine.MetricMeta("sessions", aggregation="additive", view="web_sessions", grain="session"),
        "conversion_rate": engine.MetricMeta("conversion_rate", aggregation="ratio", view="web_sessions",
                                             grain="session"),
    }
    assert engine.unit_count_metric("gross_sales", lineage) == "orders"
    assert engine.unit_count_metric("conversion_rate", lineage) == "sessions"
    assert engine.unit_count_metric("orders", lineage) is None
    assert engine.unit_count_metric("funnel_purchases", lineage) is None
