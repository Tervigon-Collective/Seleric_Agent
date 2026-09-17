"""DoWhy CI normalization and pass-through into CausalAnalysisArtifact."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from seleric_swarm.causal.dowhy_service import (
    CausalRequest,
    DoWhyEstimate,
    DoWhyService,
    _drop_collinear_common_causes,
    _extract_confidence_interval,
    _normalize_confidence_interval,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, []),
        ([0.1, 0.5], [0.1, 0.5]),
        ([0.5, 0.1], [0.1, 0.5]),
        ([[-1.0, 2.0]], [-1.0, 2.0]),
        ({"default": [0.2, 0.8]}, [0.2, 0.8]),
        ("bogus", []),
    ],
)
def test_normalize_confidence_interval(raw, expected):
    assert _normalize_confidence_interval(raw) == expected


def test_extract_via_get_confidence_intervals():
    estimate = SimpleNamespace(get_confidence_intervals=lambda: [0.05, 0.95])
    assert _extract_confidence_interval(estimate) == [0.05, 0.95]


def test_extract_returns_empty_when_getter_raises():
    estimate = SimpleNamespace(get_confidence_intervals=lambda: (_ for _ in ()).throw(RuntimeError("no ci")))
    assert _extract_confidence_interval(estimate) == []


@pytest.mark.asyncio
async def test_dowhy_estimation_passes_ci_through():
    from seleric_swarm.agents.diagnostic.registries import CausalEstimationQuery
    from seleric_swarm.agents.diagnostic.services.dowhy_estimation import (
        DoWhyCausalEstimationService,
    )

    est = DoWhyEstimate(
        treatment="t",
        outcome="y",
        effect=1.2,
        estimator="backdoor.linear_regression",
        common_causes=["z"],
        refutations=[],
        n_rows=100,
        confidence_interval=[0.4, 2.0],
    )
    mock_svc = MagicMock()
    mock_svc.estimate.return_value = est
    service = DoWhyCausalEstimationService(dowhy=mock_svc)

    art = await service.estimate(
        CausalEstimationQuery(
            mission_id="m1",
            treatment="t",
            outcome="y",
            common_causes=["z"],
            graph_id="g1",
        ),
        observations=object(),  # truthy; actual frame unused because DoWhy is mocked
    )
    assert art.confidence_interval == [0.4, 2.0]
    assert art.estimated_effect == 1.2


def test_dowhy_service_estimate_wires_ci(monkeypatch):
    """Unit path: patch CausalModel so we never invoke real DoWhy bootstrap."""
    import pandas as pd

    class FakeEstimate:
        value = 1.5

        def get_confidence_intervals(self):
            return [1.0, 2.0]

    class FakeModel:
        def __init__(self, *args, **kwargs):
            pass

        def identify_effect(self, proceed_when_unidentifiable=True):
            return "identified"

        def estimate_effect(self, identified, method_name=None):
            return FakeEstimate()

        def refute_estimate(self, *a, **k):
            return SimpleNamespace(new_effect=1.5)

    with patch("dowhy.CausalModel", FakeModel):
        svc = DoWhyService()
        data = pd.DataFrame({"t": [0, 1, 0, 1], "y": [1.0, 2.0, 1.1, 2.2], "z": [0, 0, 1, 1]})
        out = svc.estimate(
            CausalRequest(treatment="t", outcome="y", common_causes=["z"], refuters=[]),
            data,
        )
    assert out.confidence_interval == [1.0, 2.0]
    assert out.effect == 1.5


def test_drop_collinear_common_causes_removes_near_perfect_correlate():
    """Regression for the live-traffic failure: ad-platform metrics (clicks,
    impressions, spend) move together and made ``backdoor.linear_regression``'s
    design matrix singular, which surfaced as a full DoWhyUnavailable ->
    synthetic-template fallback on a dataset that had plenty of real rows."""
    import pandas as pd

    data = pd.DataFrame(
        {
            "meta_spend": [100, 110, 120, 130, 140, 150, 160, 170],
            # near-perfectly correlated with treatment -> should be dropped
            "meta_impressions": [1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700],
            # independent -> should be kept
            "day_of_week": [0, 1, 2, 3, 4, 5, 6, 0],
        }
    )
    kept, dropped = _drop_collinear_common_causes(
        data, "meta_spend", ["meta_impressions", "day_of_week"]
    )
    assert dropped == ["meta_impressions"]
    assert kept == ["day_of_week"]


def test_drop_collinear_common_causes_keeps_all_when_none_collinear():
    import pandas as pd

    data = pd.DataFrame(
        {
            "meta_spend": [100, 130, 90, 160, 110, 140, 95, 150],
            "day_of_week": [0, 1, 2, 3, 4, 5, 6, 0],
        }
    )
    kept, dropped = _drop_collinear_common_causes(data, "meta_spend", ["day_of_week"])
    assert kept == ["day_of_week"]
    assert dropped == []
