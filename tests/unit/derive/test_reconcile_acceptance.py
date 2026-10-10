"""M2.3 acceptance — golden reconcile (Q17 shape): every subtotal through derive.

End-to-end: ``plan.execute`` materialises the query steps (a total row plus a
per-channel breakdown), then the derivation ops sum the breakdown and reconcile
its subtotal against the total — residual 0, explained share of the whole.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

from seleric_swarm.contracts.pipeline import QueryStep, ResultSet, WindowSpec
from seleric_swarm.derive import derive
from seleric_swarm.plan import execute
from seleric_swarm.semantic import MetricsQueryArgs, QueryResponse, SemanticReadModel

TZ = timezone(timedelta(hours=5, minutes=30))
WINDOWS = {"event": WindowSpec(role="event", start=date(2026, 1, 1), end=date(2026, 1, 31))}


class FakePort:
    def __init__(self, total_value: float, breakdown_rows: tuple[dict[str, Any], ...]) -> None:
        self.calls: list[MetricsQueryArgs] = []
        self._total_value = total_value
        self._breakdown_rows = breakdown_rows

    async def query(self, args: MetricsQueryArgs) -> QueryResponse:
        self.calls.append(args)
        if args.dimensions:
            return QueryResponse(rows=self._breakdown_rows)
        return QueryResponse(rows=({"net_sales": self._total_value},))


def _read_model() -> SemanticReadModel:
    return SemanticReadModel.from_payload(
        {
            "metrics": [
                {
                    "id": "net_sales",
                    "aggregation": "additive",
                    "unit": "USD",
                    "supported_dimensions": ["channel"],
                }
            ]
        }
    )


@pytest.mark.asyncio
async def test_q17_subtotals_reconcile_through_derive() -> None:
    total_step = QueryStep(
        step_id="total", metric_id="net_sales", breakdowns=(), filters=(), window_role="event", grain=None
    )
    by_channel = QueryStep(
        step_id="by_channel",
        metric_id="net_sales",
        breakdowns=("channel",),
        filters=(),
        window_role="event",
        grain=None,
    )
    port = FakePort(
        total_value=100.0,
        breakdown_rows=(
            {"channel": "organic", "net_sales": 60.0},
            {"channel": "paid", "net_sales": 40.0},
        ),
    )

    (result_total, result_channel) = await execute(
        [total_step, by_channel],
        windows=WINDOWS,
        port=port,
        read_model=_read_model(),
        as_of=datetime(2026, 1, 5, 0, 0, tzinfo=TZ),
    )

    assert isinstance(result_total, ResultSet) and result_total.error is None
    assert result_total.rows[0].value == pytest.approx(100.0)
    assert result_channel.error is None and len(result_channel.rows) == 2

    subtotal = derive("sum", [result_channel], read_model=_read_model())
    assert subtotal.error is None
    assert subtotal.value == pytest.approx(100.0)
    assert subtotal.unit == "USD"

    reconciled = derive("reconcile", [result_channel, result_total], read_model=_read_model())
    assert reconciled.error is None
    assert reconciled.value == pytest.approx(0.0)
    assert reconciled.params["explained_share"] == pytest.approx(1.0)
    assert reconciled.params["residual"] == pytest.approx(0.0)
    assert {row.dimensions["part"] for row in reconciled.rows} == {"by_channel"}