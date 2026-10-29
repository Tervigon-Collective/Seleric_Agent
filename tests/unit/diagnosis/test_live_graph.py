"""The live dependency graph and the confirmation of its mechanism edges on a brand's own history (plan D-55, D-56)."""

from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from seleric_swarm.diagnosis.adapters.legacy_estimator import PoolEstimator
from seleric_swarm.diagnosis.adapters.live_subgraph import same_quantity_of_outcome
from seleric_swarm.diagnosis.adapters.service_formatting import ServiceFormatting
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.graph import SAME_QUANTITY_KINDS, EdgeKind
from seleric_swarm.diagnosis.domain.inputs import input_from_dict, input_to_dict
from seleric_swarm.diagnosis.domain.subgraph import EdgeSpec
from seleric_swarm.diagnosis.graph.live import build_live_graph, load_priors
from seleric_swarm.diagnosis.graph.subgraph import build_subgraph
from seleric_swarm.diagnosis.operators.edge_check import verify_edges
from seleric_swarm.diagnosis.operators.pipeline import EngineEnv, run_stages
from tests.unit.diagnosis import engine_world as world

pytestmark = pytest.mark.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "eval" / "diag" / "fixtures" / "brand20_daily_2026-10.json"
CORE_GRAPH = Path(os.environ.get("SELERIC_CORE_GRAPH") or "/opt/seleric/Seleric_Agent_Core/doc/dependency_graph/dependency_graph.json")


@pytest.fixture(scope="module")
def definitions():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["definitions"]


@pytest.fixture(autouse=True)
def in_process_estimates(monkeypatch):
    monkeypatch.setenv("DIAGNOSIS_WORKERS", "0")


# ----------------------------------------------------------------------------------------- the live graph
def test_the_arithmetic_is_read_from_the_catalogue_definitions(definitions):
    graph = build_live_graph(definitions).graph
    into = {e.source: e for e in graph.edges_into("net_sales")}
    assert {"gross_sales", "discounts", "net_sales_deductions"} <= set(into)
    assert into["discounts"].kind is EdgeKind.COMPOSITION and into["discounts"].coefficient == -1
    roas = {e.source: e.role for e in graph.edges_into("net_roas", frozenset({EdgeKind.RATIO_COMPONENT}))}
    assert roas == {"contribution_margin": "numerator", "ad_spend": "denominator"}


def test_a_definition_that_changes_changes_the_graph_with_no_file_to_re_vendor(definitions):
    changed = json.loads(json.dumps(definitions))
    changed["net_sales"]["formula"]["composition"] = [{"metric": "gross_sales", "sign": 1}]
    changed["net_sales"]["formula"]["depends_on"] = ["gross_sales"]
    before = build_live_graph(definitions).graph
    after = build_live_graph(changed).graph
    assert {e.source for e in before.edges_into("net_sales")} > {e.source for e in after.edges_into("net_sales")}
    assert before.version != after.version


def test_prior_edges_that_name_a_metric_the_catalogue_lacks_are_left_out_and_reported(definitions):
    smaller = {m: d for m, d in definitions.items() if m != "orders"}
    loaded = build_live_graph(smaller)
    assert "orders" not in loaded.graph.nodes and not [e for e in loaded.graph.edges if "orders" in (e.source, e.target)]
    gone = [i for i in loaded.issues if i.code == "prior_edge_not_in_catalogue"]
    assert gone and all("orders" in i.detail for i in gone)


def test_it_is_cheap_and_small(definitions):
    began = time.perf_counter()
    graph = build_live_graph(definitions).graph
    assert time.perf_counter() - began < 0.25 and len(graph.nodes) == len(definitions)
    priors = load_priors()
    assert len(priors["causal"]) == 40 and (ROOT / "src/seleric_swarm/diagnosis/data/priors.json").stat().st_size < 30_000


def test_the_priors_file_is_what_the_script_makes_from_core():
    if not CORE_GRAPH.exists():
        pytest.skip("Agent Core is not checked out here")
    import importlib.util

    spec = importlib.util.spec_from_file_location("vendor_priors", ROOT / "scripts" / "vendor_priors.py")
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    text = CORE_GRAPH.read_text(encoding="utf-8")
    import hashlib

    made = module.build(json.loads(text), hashlib.sha256(text.encode()).hexdigest())
    assert made == json.loads(json.dumps(load_priors()))  # re-vendor after Core changes: `python3 scripts/vendor_priors.py`


def test_the_live_graph_gives_the_candidates_the_vendored_one_gave(definitions):
    from seleric_swarm.diagnosis.adapters.catalogue_graph import VendoredGraphAdapter

    old, live = VendoredGraphAdapter().load().graph, build_live_graph(definitions).graph
    for outcome in ("net_sales", "ctr", "sessions", "funnel_purchases"):
        have = lambda m: None if m in definitions else "not_in_catalogue"
        assert [c.metric for c in build_subgraph(old, outcome, usable=have).candidates] == [c.metric for c in build_subgraph(live, outcome).candidates]


def test_a_product_outcome_is_never_explained_by_its_own_total(definitions):
    graph = build_live_graph(definitions).graph
    sub = build_subgraph(graph, "product_ad_spend") if "product_ad_spend" in graph.nodes else None
    if sub is None:  # the eval catalogue has no product metrics: use the full priors on a catalogue that does
        full = {m: {"cube_mapping": {"view": "x"}, "aggregation": "additive"} for m in ("ad_spend", "product_ad_spend", "impressions")}
        graph = build_live_graph(full).graph
        sub = build_subgraph(graph, "product_ad_spend")
    assert "ad_spend" not in [c.metric for c in sub.candidates]
    assert "ad_spend" in {p.peer for p in sub.peers}
    assert same_quantity_of_outcome(sub, ["ad_spend", "impressions"]) == ["impressions"]


def test_no_causal_edge_joins_two_measurements_of_one_quantity():
    priors = load_priors()
    same = {frozenset((p["a"], p["b"])) for p in priors["pairs"] if p["kind"] != "subset"}
    subsets = set()
    # (a subset is part of the same events, and Core allows a mechanism between a total and its subset: `orders -> cod_orders`)
    assert not [e for e in priors["causal"] if frozenset((e["source"], e["target"])) in same and (e["source"], e["target"]) not in subsets]
    assert {p["kind"] for p in priors["pairs"]} <= {k.value for k in SAME_QUANTITY_KINDS}


# ------------------------------------------------------------------------------- confirming an edge on history
DAYS = [date(2026, 8, 1) + timedelta(days=i) for i in range(70)]


def _series(values):
    return {d: float(v) for d, v in zip(DAYS, values, strict=True)}


def _pair(*, effect: float, seed: int = 3):
    rng = np.random.default_rng(seed)
    source = 100 + 20 * rng.normal(size=len(DAYS))
    target = 50 + effect * source + 5 * rng.normal(size=len(DAYS)) + 3 * np.sin(np.arange(len(DAYS)) * 2 * np.pi / 7)
    return {"s": _series(source), "t": _series(target)}


def _spec(sign="+", lag=(0, 0), confidence="strong"):
    return EdgeSpec("s", "t", sign, lag, confidence)


def test_an_edge_the_history_shows_is_confirmed():
    [verdict] = verify_edges([_spec()], _pair(effect=0.8), DAYS, DiagnosisConfig()).values()
    assert verdict.status == "verified" and verdict.usable and verdict.beta and verdict.beta > 0.5 and verdict.n_rows >= 60


def test_an_edge_with_no_effect_is_not_confirmed_and_one_with_the_opposite_sign_says_so():
    [flat] = verify_edges([_spec()], _pair(effect=0.0, seed=8), DAYS, DiagnosisConfig()).values()
    assert flat.status == "not_confirmed" and not flat.usable
    [opposite] = verify_edges([_spec()], _pair(effect=-0.8), DAYS, DiagnosisConfig()).values()
    assert opposite.status == "wrong_sign" and not opposite.usable
    [either] = verify_edges([_spec(sign="+/-")], _pair(effect=-0.8), DAYS, DiagnosisConfig()).values()
    assert either.status == "verified"


def test_a_source_that_never_varies_is_unverifiable_not_failed():
    data = _pair(effect=0.8)
    data["s"] = {d: (5.0 if i % 20 else 0.0) for i, d in enumerate(DAYS)}  # an edit count: almost always the same
    [verdict] = verify_edges([_spec()], data, DAYS, DiagnosisConfig()).values()
    assert verdict.status == "no_variation" and not verdict.usable and "nothing to learn" in verdict.describe()


def test_missing_data_and_a_short_history_are_unverifiable():
    cfg = DiagnosisConfig()
    [no_data] = verify_edges([_spec()], {"s": _pair(effect=1)["s"]}, DAYS, cfg).values()
    assert no_data.status == "no_series"
    [short] = verify_edges([_spec()], _pair(effect=0.8), DAYS[:15], cfg).values()
    assert short.status == "too_little_data"


def test_a_structural_edge_is_a_definition_and_is_not_tested():
    [verdict] = verify_edges([_spec(confidence="structural")], {}, DAYS, DiagnosisConfig()).values()
    assert verdict.status == "definitional" and verdict.usable


def test_the_lag_window_is_searched_and_its_cost_is_paid():
    rng = np.random.default_rng(5)
    source = 100 + 20 * rng.normal(size=len(DAYS))
    target = [50.0] * len(DAYS)
    for i in range(2, len(DAYS)):
        target[i] = 50 + 0.9 * source[i - 2] + 4 * rng.normal()
    [verdict] = verify_edges([_spec(lag=(0, 3))], {"s": _series(source), "t": _series(target)}, DAYS, DiagnosisConfig()).values()
    assert verdict.status == "verified" and verdict.lag == 2


def test_the_false_discovery_correction_is_across_the_edges_tested():
    weak = _pair(effect=0.0, seed=21)
    series = {**weak, "u": weak["s"]}
    specs = [_spec(), EdgeSpec("u", "t", "+", (0, 0), "strong")]
    verdicts = verify_edges(specs, series, DAYS, DiagnosisConfig())
    assert all(v.p_adj >= v.p_raw for v in verdicts.values() if v.p_raw is not None)


# ------------------------------------------------------------------------------- WHY_EVENT uses only confirmed edges
def _report(inp):
    env = EngineEnv(DiagnosisConfig(), PoolEstimator(), ServiceFormatting(), time.monotonic)
    return run_stages(inp, env).report


def _chained(inp, *, edge_ok: bool):
    """spend -> visits -> orders -> sales (the engine world), chains as the graph would give them."""
    inp.candidates_from_graph = True
    inp.driver_chains = {"m_spend": [["m_spend", "m_visits", "+", 0, 0, "strong"], ["m_visits", "m_orders", "+", 0, 0, "strong"]]}
    if not edge_ok:
        rng = np.random.default_rng(4)
        inp.series["m_orders"] = {d: float(50 + 10 * rng.normal()) for d in inp.series["m_orders"]}  # visits no longer drive orders
    return inp


def test_a_candidate_on_a_confirmed_path_is_estimated_and_the_edges_are_in_the_report():
    report = _report(_chained(world.engine_input(), edge_ok=True))
    spend = next(d for d in report.drivers if d.driver == "m_spend")  # type: ignore[union-attr]
    assert spend.status != "excluded"
    assert {(c.source, c.target, c.status) for c in report.edge_checks} == {("m_spend", "m_visits", "verified"), ("m_visits", "m_orders", "verified")}  # type: ignore[union-attr]
    assert not [n for n in report.data_quality if "could not be checked" in n]  # type: ignore[union-attr]


def test_a_candidate_whose_path_the_history_does_not_confirm_is_excluded_and_the_answer_says_so_once():
    report = _report(_chained(world.engine_input(), edge_ok=False))
    spend = next(d for d in report.drivers if d.driver == "m_spend")  # type: ignore[union-attr]
    assert spend.status == "excluded" and "could not be confirmed on this brand's history" in spend.reason
    notes = [n for n in report.data_quality if n.startswith("could not be checked on your data")]  # type: ignore[union-attr]
    assert notes == ["could not be checked on your data: m_visits -> m_orders: no effect found in the history"]


def test_only_the_cap_of_confirmed_candidates_is_estimated_and_the_rest_are_counted():
    inp = world.engine_input(drivers=("m_spend", "m_visits"))
    inp.candidates_from_graph = True
    inp.driver_chains = {
        "m_spend": [["m_spend", "m_visits", "+", 0, 0, "strong"]],
        "m_visits": [["m_visits", "m_orders", "+", 0, 0, "strong"]],
    }
    inp.estimate_cap = 1
    report = _report(inp)
    estimated = [d for d in report.drivers if d.status != "excluded"]  # type: ignore[union-attr]
    assert len(estimated) == 1
    assert any("not estimated (limit 1)" in n for n in report.data_quality)  # type: ignore[union-attr]


def test_the_chains_and_the_cap_survive_the_engine_input_text_form():
    inp = _chained(world.engine_input(), edge_ok=True)
    inp.estimate_cap = 3
    again = input_from_dict(json.loads(json.dumps(input_to_dict(inp))))
    assert again.driver_chains == inp.driver_chains and again.estimate_cap == 3
    raw = input_to_dict(inp)
    del raw["driver_chains"], raw["estimate_cap"]
    assert input_from_dict(raw).driver_chains == {} and input_from_dict(raw).estimate_cap == 0


# ------------------------------------------------------------------- the graph supplies the direction the history cannot
def _no_shocks():
    return world.engine_input(shocks=0)  # spend never switches in the history: its direction cannot be shown by the data


def test_without_the_graph_a_driver_with_no_direction_evidence_stays_an_association():
    report = _report(_no_shocks())
    spend = next(d for d in report.drivers if d.driver == "m_spend")  # type: ignore[union-attr]
    assert spend.status == "implicated" and spend.classification == "correlation"
    assert spend.direction_evidence.get("direction") == "assumed"


def test_the_root_of_an_abnormal_move_on_a_confirmed_path_becomes_a_likely_contributor_never_a_supported_cause():
    report = _report(_chained(_no_shocks(), edge_ok=True))
    spend = next(d for d in report.drivers if d.driver == "m_spend")  # type: ignore[union-attr]
    assert spend.classification == "likely_contributor" and abs(spend.driver_z or 0) >= DiagnosisConfig().outlier_z
    assert "reviewed mechanism (m_spend -> m_visits -> m_orders" in spend.reason and "nothing upstream of it did" in spend.reason
    assert report.verdict in ("partially_explained", "explained")  # type: ignore[union-attr]


def test_something_abnormal_upstream_on_a_confirmed_path_takes_the_name_instead():
    inp = _chained(_no_shocks(), edge_ok=True)
    rng = np.random.default_rng(2)
    inp.series["m_up"] = {d: 2 * v + float(rng.normal(0, 20)) for d, v in inp.series["m_spend"].items()}  # drives spend, falls with it
    inp.lineage["m_up"] = inp.lineage["m_spend"]
    inp.candidate_drivers = ["m_spend", "m_up"]
    inp.driver_chains["m_up"] = [["m_up", "m_spend", "+", 0, 0, "strong"], *inp.driver_chains["m_spend"]]
    drivers = {d.driver: d for d in _report(inp).drivers}  # type: ignore[union-attr]
    assert drivers["m_spend"].classification == "correlation"  # explained by the abnormal metric upstream of it
    assert drivers["m_up"].status != "excluded"


def test_an_upstream_metric_whose_own_path_is_not_confirmed_does_not_block_the_name():
    inp = _chained(_no_shocks(), edge_ok=True)
    rng = np.random.default_rng(9)
    inp.series["m_up"] = {d: float(100 + rng.normal(0, 5)) for d in inp.series["m_spend"]}
    last = world.DAYS[-1]
    inp.series["m_up"][last] = 400.0  # abnormal on the event day, but nothing ties it to spend
    inp.lineage["m_up"] = inp.lineage["m_spend"]
    inp.candidate_drivers = ["m_spend", "m_up"]
    inp.driver_chains["m_up"] = [["m_up", "m_spend", "+", 0, 0, "strong"], *inp.driver_chains["m_spend"]]
    drivers = {d.driver: d for d in _report(inp).drivers}  # type: ignore[union-attr]
    assert drivers["m_up"].status == "excluded" and drivers["m_spend"].classification == "likely_contributor"


def test_an_ordinary_day_or_a_disagreeing_measurement_names_no_root():
    inp = _chained(_no_shocks(), edge_ok=True)
    env = EngineEnv(DiagnosisConfig(), PoolEstimator(), ServiceFormatting(), time.monotonic)
    part = run_stages(inp, env, until="what")
    from dataclasses import replace

    from seleric_swarm.diagnosis.operators.why_event import run_why_event

    ordinary = replace(part.event.analysis, unusual=False, notable=False)  # type: ignore[union-attr]
    drivers = {d.driver: d for d in run_why_event(ordinary, part.what, cfg=env.config, estimator=env.estimator).drivers}  # type: ignore[arg-type]
    assert drivers["m_spend"].classification == "correlation"


def test_the_priors_carry_the_business_model_the_agent_reads():
    bm = load_priors()["business_model"]
    funnel = next(iter(bm["processes"].values()))
    stages = [s["id"] for s in funnel["stages"]]
    assert len(stages) >= 3 and stages[0] != stages[-1]
    assert all({"events", "session_share", "step_rate", "ledger"} & set(s) for s in funnel["stages"])
    assert all(t["daily"] != t["hourly"] and t["metrics"] for t in bm["grain_twins"])
    assert all(set(e["keys_by_view"]) for e in bm["entities"].values())
    assert bm["policies"]["periods"]["state_window"]


def test_customers_is_the_orders_counted_another_way_and_is_never_offered_as_its_cause(definitions):
    """r74 (2026-10-29): 'Customers is a cause of orders, all refuters pass'. The pair is a reviewed co-measure."""
    if "customers" not in definitions:
        pytest.skip("the fixture catalogue has no customers metric")
    sub = build_subgraph(build_live_graph(definitions).graph, "orders")
    assert not any(c.metric == "customers" for c in sub.candidates)
    assert ("customers", "co_measure") in {(p.peer, p.kind) for p in sub.peers}
