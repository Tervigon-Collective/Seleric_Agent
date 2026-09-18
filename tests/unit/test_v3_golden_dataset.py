from __future__ import annotations

from seleric_swarm.evals.golden_dataset import load_golden_dataset


def test_loads_lookup_commerce_cases() -> None:
    cases = load_golden_dataset()
    assert cases
    assert all(case.query for case in cases)
    assert any(case.expected.get("metric_id") == "metric.net_sales" for case in cases)
