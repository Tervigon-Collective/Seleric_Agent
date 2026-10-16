"""A candidate driver whose effect must pass through other metrics (the reviewed graph's path to the outcome) is ruled
out when one of them stayed quiet at the event (plan P3.2). Generic ids, no catalogue names."""

from __future__ import annotations

import time
from copy import deepcopy

import pytest

from seleric_swarm.diagnosis.adapters.legacy_estimator import PoolEstimator
from seleric_swarm.diagnosis.adapters.service_formatting import ServiceFormatting
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.inputs import input_from_dict, input_to_dict
from seleric_swarm.diagnosis.operators.pipeline import EngineEnv, run_stages
from tests.unit.diagnosis import engine_world as world

pytestmark = pytest.mark.filterwarnings("ignore")
PATH = ["m_visits", "m_orders"]  # spend -> visits -> orders -> sales


@pytest.fixture(autouse=True)
def in_process_estimates(monkeypatch):
    monkeypatch.setenv("DIAGNOSIS_WORKERS", "0")


def _drivers(inp):
    env = EngineEnv(DiagnosisConfig(), PoolEstimator(), ServiceFormatting(), time.monotonic)
    report = run_stages(inp, env).report
    return {d.driver: d for d in report.drivers}  # type: ignore[union-attr]


def test_a_driver_whose_path_moved_is_judged_as_before():
    inp = world.engine_input()
    inp.driver_paths = {"m_spend": list(PATH)}
    with_path = _drivers(inp)["m_spend"]
    inp.driver_paths = {}
    without = _drivers(inp)["m_spend"]
    assert with_path.ruled_out_because != "path_did_not_move"
    assert (with_path.status, with_path.classification) == (without.status, without.classification)


def test_a_driver_is_ruled_out_when_a_metric_on_its_path_stayed_quiet():
    inp = world.engine_input()
    last = world.DAYS[-1]
    quiet = deepcopy(inp)
    visits = quiet.series["m_visits"]
    normal = sorted(visits[d] for d in world.DAYS[-31:-2])[14]  # the median of the last month: its normal level
    visits[world.DAYS[-2]] = visits[last] = normal  # visits keep their normal level on the day before and on the event day
    quiet.driver_paths = {"m_spend": list(PATH)}
    found = _drivers(quiet)["m_spend"]
    assert found.status == "ruled_out" and found.ruled_out_because == "path_did_not_move"
    assert "m_visits did not move unusually" in found.reason and "m_visits, m_orders" in found.reason
    assert found.driver_moved_before_or_at_event is True and found.driver_z is not None  # the driver itself did move


def test_a_metric_on_the_path_with_no_series_gives_the_benefit_of_the_doubt():
    inp = world.engine_input()
    inp.driver_paths = {"m_spend": ["m_not_fetched", *PATH]}  # no data for the first: skipped, the others still checked
    assert _drivers(inp)["m_spend"].ruled_out_because != "path_did_not_move"


def test_the_paths_survive_the_engine_input_text_form_and_old_inputs_read_without_them():
    inp = world.engine_input()
    inp.driver_paths = {"m_spend": list(PATH)}
    raw = input_to_dict(inp)
    assert input_from_dict(raw).driver_paths == {"m_spend": PATH}
    del raw["driver_paths"]
    assert input_from_dict(raw).driver_paths == {}


def test_no_cause_is_accepted_for_a_change_inside_its_normal_range():
    from dataclasses import replace

    from seleric_swarm.diagnosis.operators.why_event import run_why_event

    env = EngineEnv(DiagnosisConfig(), PoolEstimator(), ServiceFormatting(), time.monotonic)
    part = run_stages(world.engine_input(), env, until="what")
    ana = part.event.analysis  # type: ignore[union-attr]
    real = {d.driver: d for d in run_why_event(ana, part.what, cfg=env.config, estimator=env.estimator).drivers}["m_spend"]  # type: ignore[arg-type]
    assert real.classification in ("supported_cause", "likely_contributor")
    ordinary = replace(ana, unusual=False, notable=False)
    capped = {d.driver: d for d in run_why_event(ordinary, part.what, cfg=env.config, estimator=env.estimator).drivers}["m_spend"]  # type: ignore[arg-type]
    assert capped.classification == "correlation" and "no cause is accepted" in capped.reason
    assert capped.share_of_change == real.share_of_change  # what was found is still reported, only the label changes


def test_the_graph_chosen_list_is_not_screened_again_by_lockstep_or_name_tokens():
    inp = world.engine_input(drivers=("m_spend", "m_twin", "m_units"))
    inp.series["m_twin"] = {d: v * 1.1 for d, v in inp.series["m_orders"].items()}  # moves in step with a component
    inp.lineage["m_twin"] = type(inp.lineage["m_orders"])("m_twin", "additive", "web_view", (), grain="brand_visit_day")
    inp.series["m_units"] = {d: v for d, v in inp.series["m_spend"].items()}
    inp.lineage["m_units"] = type(inp.lineage["m_orders"])("m_units", "additive", "ads_view", (), grain="brand_order_day")  # same unit tokens as the outcome
    screened = _drivers(deepcopy(inp))
    assert screened["m_twin"].status == "excluded" and "counts the same events" in screened["m_twin"].reason
    assert screened["m_units"].status == "excluded" and "same units" in screened["m_units"].reason
    inp.candidates_from_graph = True
    from_graph = _drivers(inp)
    assert from_graph["m_twin"].status != "excluded" and from_graph["m_units"].status != "excluded"
