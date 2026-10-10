"""The eval reads the v2 spec from the mission trace and sends truth as metrics_query args."""

from __future__ import annotations

from seleric_swarm.evals._case import load_cases
from seleric_swarm.evals._runner import _observed_from_payload, truth_query


def test_spec_falls_back_to_v2_trace() -> None:
    case = load_cases("eval/golden/lookup.yaml")[0]
    spec = {"kind": "analysis", "measures": [{"metric_id": "net_sales"}]}
    obs = _observed_from_payload(case, "q", {"status": "completed", "trace": {"v2_shadow": {"spec": spec}}})
    assert obs.spec == spec
    first = _observed_from_payload(case, "q", {"trace": {"spec": {"kind": "x"}, "v2_shadow": {"spec": spec}}})
    assert first.spec == {"kind": "x"}


def test_truth_query_shape() -> None:
    assert truth_query({"metric_id": "net_sales", "start": "2026-09-02", "end": "2026-09-02"}) == {
        "measures": ["net_sales"], "time_range": {"start": "2026-09-02", "end": "2026-09-02"}}
    assert truth_query({"measures": ["x"]}) == {"measures": ["x"]}
