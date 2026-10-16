"""MEASUREMENT (plan P3.2): two systems that count the same events (a co-measure pair of the reviewed graph) are compared on
the event days, and a divergence comes ahead of any behavioural cause. Generic ids."""

from __future__ import annotations

import time
from copy import deepcopy

import numpy as np
import pytest

from seleric_swarm.diagnosis.adapters.legacy_estimator import PoolEstimator
from seleric_swarm.diagnosis.adapters.service_formatting import ServiceFormatting
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.inputs import input_from_dict, input_to_dict
from seleric_swarm.diagnosis.domain.lineage import MetricMeta
from seleric_swarm.diagnosis.domain.report import MeasurementFinding
from seleric_swarm.diagnosis.operators.pipeline import EngineEnv, run_stages
from seleric_swarm.diagnosis.trace import events as kinds
from seleric_swarm.diagnosis.trace.report_events import emit_report_events
from tests.unit.diagnosis import engine_world as world

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture(autouse=True)
def in_process_estimates(monkeypatch):
    monkeypatch.setenv("DIAGNOSIS_WORKERS", "0")


def _world(*, peer_follows: bool):
    """The engine world plus a second system counting the same orders. On the event day the other system either follows
    (a real change in orders) or does not (one of the two systems is wrong on the event day)."""
    inp = world.engine_input()
    rng = np.random.default_rng(11)
    orders = inp.series["m_orders"]
    peer = {d: v * 1.1 * float(1 + rng.normal(0, 0.03)) for d, v in orders.items()}
    last = world.DAYS[-1]
    if not peer_follows:
        peer[last] = 1.6 * sorted(peer[d] for d in world.DAYS[-31:-1])[15]  # the other system counted far more on the event day
    inp.series["m_peer"] = peer
    inp.lineage["m_peer"] = MetricMeta("m_peer", "additive", "web_view", (), grain="brand_visit_day")
    inp.measurement_pairs = [("m_orders", "m_peer", "agree")]
    return inp


def _report(inp, *, acts=True):
    env = EngineEnv(DiagnosisConfig(measurement_acts=acts), PoolEstimator(), ServiceFormatting(), time.monotonic)
    return run_stages(inp, env).report


def test_two_systems_that_follow_each_other_agree_and_nothing_changes():
    [found] = _report(_world(peer_follows=True)).measurement  # type: ignore[union-attr]
    assert found.status == "agrees" and found.n_history >= 8 and abs(found.z or 0) < DiagnosisConfig().measurement_z


def test_a_divergence_is_found_named_and_comes_ahead_of_any_cause():
    follows = _report(_world(peer_follows=True))
    broken = _report(_world(peer_follows=False))
    [found] = broken.measurement  # type: ignore[union-attr]
    assert found.status == "diverged" and found.gap is not None and found.gap < -0.25 and abs(found.z or 0) >= 5.0
    assert found.metric_move is not None and found.peer_move is not None and found.metric_move < found.peer_move
    assert "m_orders and m_peer count the same events" in found.reason
    # no behavioural cause is accepted while the two disagree; what the regression found is still reported
    assert not any(d.classification in ("supported_cause", "likely_contributor") for d in broken.drivers)  # type: ignore[union-attr]
    assert any("count the same events and disagree" in d.reason for d in broken.drivers)  # type: ignore[union-attr]
    assert any(d.classification in ("supported_cause", "likely_contributor") for d in follows.drivers)  # type: ignore[union-attr]
    assert any(h["status"] == "supported" and "mis-measured" in h["hypothesis"] for h in broken.competing_hypotheses)  # type: ignore[union-attr]
    assert "disagrees with m_peer" in broken.headline and any(line.startswith("MEASUREMENT:") for line in broken.narrative)  # type: ignore[union-attr]
    assert {"m_orders", "m_peer"} <= set(broken.event_values)  # the answer may cite both numbers


def test_the_divergence_is_an_event_in_the_trace():
    broken = _report(_world(peer_follows=False))
    seen: list[tuple[str, dict]] = []
    emit_report_events(lambda kind, payload, parent: seen.append((kind, dict(payload))) or len(seen), broken)  # type: ignore[arg-type]
    [event] = [p for k, p in seen if k == kinds.HYPOTHESIS_RESOLVED and p["hypothesis"] == "measurement:m_orders~m_peer"]
    assert event["status"] == "supported" and event["label"] == "measurement" and event["z"] < -5


def test_too_little_history_is_not_a_finding():
    inp = _world(peer_follows=False)
    inp.series["m_peer"] = {d: v for d, v in inp.series["m_peer"].items() if d >= world.DAYS[-6]}
    [found] = _report(inp).measurement  # type: ignore[union-attr]
    assert found.status == "insufficient_data" and isinstance(found, MeasurementFinding)


def test_a_ratio_that_is_always_the_same_does_not_make_a_flat_day_a_divergence():
    inp = world.engine_input()
    inp.series["m_peer"] = {d: v * 2.0 for d, v in inp.series["m_orders"].items()}  # exactly proportional: zero spread
    inp.lineage["m_peer"] = MetricMeta("m_peer", "additive", "web_view", (), grain="brand_visit_day")
    inp.measurement_pairs = [("m_orders", "m_peer", "agree")]
    [found] = _report(inp).measurement  # type: ignore[union-attr]
    assert found.status == "agrees"  # the spread is floored, and the gap is zero


def test_pairs_survive_the_engine_input_text_form_and_old_inputs_read_without_them():
    inp = _world(peer_follows=True)
    raw = input_to_dict(deepcopy(inp))
    assert input_from_dict(raw).measurement_pairs == [("m_orders", "m_peer", "agree")]
    del raw["measurement_pairs"]
    assert input_from_dict(raw).measurement_pairs == []


def test_until_it_is_calibrated_a_divergence_is_recorded_and_changes_nothing_else():
    shadow, acting = _report(_world(peer_follows=False), acts=False), _report(_world(peer_follows=False))
    assert [f.status for f in shadow.measurement] == ["diverged"]  # type: ignore[union-attr]
    assert any(d.classification in ("supported_cause", "likely_contributor") for d in shadow.drivers)  # type: ignore[union-attr]
    assert not any(d.classification in ("supported_cause", "likely_contributor") for d in acting.drivers)  # type: ignore[union-attr]
    assert "disagrees" not in shadow.headline and not any(line.startswith("MEASUREMENT:") for line in shadow.narrative)  # type: ignore[union-attr]
    assert not any("mis-measured" in h["hypothesis"] for h in shadow.competing_hypotheses)  # type: ignore[union-attr]
    assert DiagnosisConfig().measurement_acts is False
