"""Engine router fallback with Chronos down (httpx mock)."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from seleric_swarm.forecasting.engines import route_and_forecast
from seleric_swarm.forecasting.types import FeatureFrame
from seleric_swarm.models.service import ForecastUnavailable


def _frame(n_hist: int = 40, horizon: int = 7) -> FeatureFrame:
    start = date(2026, 8, 1)
    cutoff = start + timedelta(days=n_hist - 1)
    h_start = cutoff + timedelta(days=1)
    h_end = h_start + timedelta(days=horizon - 1)
    values = [10.0 + (i % 7) for i in range(n_hist)]
    return FeatureFrame(
        cutoff=cutoff,
        context_start=start,
        horizon_start=h_start,
        horizon_end=h_end,
        index=[(start + timedelta(days=i)).isoformat() for i in range(n_hist + horizon)],
        targets={"net_sales": values},
    )


@pytest.mark.asyncio
async def test_router_falls_back_to_ets_when_chronos_down():
    frame = _frame()

    class _Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            raise httpx.ConnectError("down")

    with patch("seleric_swarm.forecasting.engines.httpx.AsyncClient", return_value=_Boom()):
        result, decision = await route_and_forecast(
            frame,
            target_id="net_sales",
            preferred_engine="chronos-2",
            chronos_url="http://127.0.0.1:9",
            ets_model_id="forecast.net_sales.daily",
            nonnegative=True,
            provisional=True,
        )
    assert result.engine == "ets"
    assert decision.reason in {"fallback_ets", "ets_approved"}
    assert "chronos-2" in decision.tried
    assert len(result.days) == 7


@pytest.mark.asyncio
async def test_router_refuses_when_history_too_thin_for_any_engine():
    frame = _frame(n_hist=2)
    with pytest.raises(ForecastUnavailable):
        await route_and_forecast(
            frame,
            target_id="net_sales",
            preferred_engine="chronos-2",
            chronos_url=None,
            ets_model_id=None,
            provisional=True,
        )


@pytest.mark.asyncio
async def test_chronos_success_path():
    frame = _frame(horizon=3)
    v2_payload = {
        "model_id": "amazon/chronos-2",
        "revision": "abc",
        "tasks": [
            {
                "task_id": "net_sales",
                "targets": {
                    "net_sales": {
                        "forecast": [
                            {"timestamp": "2026-09-10", "mean": 11, "p10": 9, "p50": 11, "p90": 13},
                            {"timestamp": "2026-09-11", "mean": 12, "p10": 10, "p50": 12, "p90": 14},
                            {"timestamp": "2026-09-12", "mean": 13, "p10": 11, "p50": 13, "p90": 15},
                        ]
                    }
                },
            }
        ],
    }

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = lambda: None
    mock_resp.json = lambda: v2_payload

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, *a, **k):
            assert url.endswith("/v2/forecast")
            return mock_resp

    with patch("seleric_swarm.forecasting.engines.httpx.AsyncClient", return_value=_Client()):
        result, decision = await route_and_forecast(
            frame,
            target_id="net_sales",
            preferred_engine="chronos-2",
            chronos_url="http://chronos:8112",
            ets_model_id=None,
            provisional=True,
        )
    assert result.engine == "chronos-2"
    assert decision.engine == "chronos-2"
    assert len(result.days) == 3
    assert result.days[0].p50 == 11


@pytest.mark.asyncio
async def test_chronos_v2_404_falls_back_to_predict():
    frame = _frame(horizon=2)
    v1_payload = {
        "model_id": "amazon/chronos-2",
        "forecasts": [
            {
                "forecast": [
                    {"timestamp": "2026-09-10", "mean": 5, "p10": 4, "p50": 5, "p90": 6},
                    {"timestamp": "2026-09-11", "mean": 6, "p10": 5, "p50": 6, "p90": 7},
                ]
            }
        ],
    }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, *a, **k):
            resp = AsyncMock()
            if url.endswith("/v2/forecast"):
                resp.status_code = 404
                resp.raise_for_status = lambda: None
                return resp
            resp.status_code = 200
            resp.raise_for_status = lambda: None
            resp.json = lambda: v1_payload
            return resp

    with patch("seleric_swarm.forecasting.engines.httpx.AsyncClient", return_value=_Client()):
        result, _ = await route_and_forecast(
            frame,
            target_id="net_sales",
            preferred_engine="chronos-2",
            chronos_url="http://chronos:8112",
            ets_model_id=None,
            provisional=True,
        )
    assert result.days[0].p50 == 5
