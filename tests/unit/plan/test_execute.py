"""QueryStep executor unit tests (M2.2)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest

from seleric_swarm.contracts.pipeline import (
    FilterSpec,
    QueryStep,
    RankingSpec,
    ResultSet,
    WindowSpec,
)
from seleric_swarm.plan import execute
from seleric_swarm.semantic import (
    CatalogueError,
    MetricsQueryArgs,
    QueryResponse,
    SemanticReadModel,
)
from seleric_swarm.services.elapsed import ELAPSED_KEY

TZ = timezone(timedelta(hours=5, minutes=30))

WINDOWS = {
    "event": WindowSpec(role="event", start=date(2026, 1, 1), end=date(2026, 1, 31)),
    "baseline": WindowSpec(role="baseline", start=date(2025, 12, 1), end=date(2025, 12, 31)),
}


class FakePort:
    def __init__(self) -> None:
        self.calls: list[MetricsQueryArgs] = []
        self.rows: dict[str, tuple[dict[str, object], ...]] = {}
        self.failing: set[str] = set()

    async def query(self, args: MetricsQueryArgs) -> QueryResponse:
        self.calls.append(args)
        metric = args.measures[0]
        if metric in self.failing:
            return QueryResponse(error=CatalogueError(code="QUERY_FAILED", message="boom"))
        return QueryResponse(rows=self.rows.get(metric, ()))


class RecordingStore:
    def __init__(self) -> None:
        self.puts: list[tuple[str, dict[str, Any]]] = []

    def put_evidence(self, payload: dict[str, Any], evidence_id: str) -> None:
        self.puts.append((evidence_id, payload))


def _read_model() -> SemanticReadModel:
    return SemanticReadModel.from_payload(
        {
            "metrics": [
                {
                    "id": "net_sales",
                    "aggregation": "additive",
                    "unit": "USD",
                    "supported_dimensions": ["channel"],
                },
                {"id": "orders", "aggregation": "additive", "unit": "count"},
            ]
        }
    )


async def _run(
    plan: list[QueryStep],
    port: FakePort,
    _as_of: datetime | None = None,
    evidence_store: RecordingStore | None = None,
) -> tuple[ResultSet, ...]:
    return await execute(
        plan,
        windows=WINDOWS,
        port=port,
        read_model=_read_model(),
        as_of=_as_of or datetime(2026, 1, 5, 0, 0, tzinfo=TZ),
        evidence_store=evidence_store,
    )


@pytest.mark.asyncio
async def test_parallel_and_entity_dependent_steps() -> None:
    port = FakePort()
    port.rows["net_sales"] = (
        {"channel": "organic", "net_sales": 60.0},
        {"channel": "paid", "net_sales": 40.0},
    )
    plan = [
        QueryStep(step_id="s1", metric_id="net_sales", breakdowns=("channel",), filters=(), window_role="event", grain=None),
        QueryStep(step_id="s2", metric_id="net_sales", breakdowns=(), filters=(), window_role="baseline", grain=None),
        QueryStep(
            step_id="s3",
            metric_id="net_sales",
            breakdowns=(),
            filters=(),
            window_role="baseline",
            grain=None,
            entities_from_step="s1",
        ),
    ]

    results = await _run(plan, port)

    assert len(results) == 3
    assert all(r.error is None for r in results)
    assert [r.step_id for r in results] == ["s1", "s2", "s3"]
    assert len(results[0].rows) == 2
    assert all(row.evidence_id.startswith("evidence-") for row in results[0].rows)

    s3_call = port.calls[-1]
    assert {"dimension": "channel", "operator": "equals", "values": ["organic", "paid"]} in s3_call.filters


@pytest.mark.asyncio
async def test_evidence_is_recorded_with_deterministic_ids() -> None:
    port = FakePort()
    port.rows["net_sales"] = ({"channel": "organic", "net_sales": 60.0},)
    plan = [
        QueryStep(step_id="s1", metric_id="net_sales", breakdowns=("channel",), filters=(), window_role="event", grain=None)
    ]
    store = RecordingStore()

    (result,) = await _run(plan, port, evidence_store=store)

    assert result.rows[0].evidence_id in {evidence_id for evidence_id, _ in store.puts}
    payload = next(payload for evidence_id, payload in store.puts if evidence_id == result.rows[0].evidence_id)
    assert payload["metric_id"] == "net_sales"
    assert payload["dimensions"] == {"channel": "organic"}
    assert payload["unit"] == "USD"

    (second,) = await _run(plan, port, evidence_store=store)
    assert second.rows[0].evidence_id == result.rows[0].evidence_id


@pytest.mark.asyncio
async def test_partial_failure_records_stage_error_and_others_continue() -> None:
    port = FakePort()
    port.rows["net_sales"] = ({"net_sales": 100.0},)
    port.failing.add("orders")
    plan = [
        QueryStep(step_id="s1", metric_id="net_sales", breakdowns=(), filters=(), window_role="event", grain=None),
        QueryStep(step_id="s2", metric_id="orders", breakdowns=(), filters=(), window_role="event", grain=None),
        QueryStep(step_id="s3", metric_id="net_sales", breakdowns=(), filters=(), window_role="baseline", grain=None),
    ]

    results = await _run(plan, port)

    assert results[0].error is None
    assert results[1].error is not None and results[1].error.code == "QUERY_FAILED"
    assert results[2].error is None
    assert len(port.calls) == 3


@pytest.mark.asyncio
async def test_not_in_maps_to_not_equals() -> None:
    port = FakePort()
    port.rows["net_sales"] = ({"net_sales": 5.0},)
    plan = [
        QueryStep(
            step_id="s1",
            metric_id="net_sales",
            breakdowns=(),
            filters=(FilterSpec(dimension="channel", operator="not_in", values=("email",)),),
            window_role="event",
            grain=None,
        )
    ]

    results = await _run(plan, port)

    assert results[0].error is None
    assert {"dimension": "channel", "operator": "notEquals", "values": ["email"]} in port.calls[0].filters


@pytest.mark.asyncio
async def test_ranking_becomes_sort_and_limit() -> None:
    port = FakePort()
    port.rows["net_sales"] = ({"channel": "organic", "net_sales": 60.0},)
    plan = [
        QueryStep(
            step_id="s1",
            metric_id="net_sales",
            breakdowns=("channel",),
            filters=(),
            window_role="event",
            grain=None,
            ranking=RankingSpec(by_metric_id="net_sales", order="desc", limit=5),
        )
    ]

    results = await _run(plan, port)

    assert results[0].error is None
    assert port.calls[0].limit == 5
    assert port.calls[0].sort == ({"field": "net_sales", "direction": "desc"},)


@pytest.mark.asyncio
async def test_missing_window_role_is_a_stage_error() -> None:
    port = FakePort()
    plan = [
        QueryStep(step_id="s1", metric_id="net_sales", breakdowns=(), filters=(), window_role="context", grain=None)
    ]

    results = await _run(plan, port)

    assert results[0].error is not None and results[0].error.code == "unknown_window_role"
    assert port.calls == []


@pytest.mark.asyncio
async def test_dependent_step_with_failed_referent_is_an_error() -> None:
    port = FakePort()
    port.failing.add("net_sales")
    plan = [
        QueryStep(step_id="s1", metric_id="net_sales", breakdowns=("channel",), filters=(), window_role="event", grain=None),
        QueryStep(
            step_id="s2",
            metric_id="net_sales",
            breakdowns=(),
            filters=(),
            window_role="baseline",
            grain=None,
            entities_from_step="s1",
        ),
    ]

    results = await _run(plan, port)

    assert results[0].error is not None
    assert results[1].error is not None and results[1].error.code == "step_dependency_failed"


@pytest.mark.asyncio
async def test_elapsed_only_cuts_the_running_day_to_elapsed_hours() -> None:
    as_of = datetime(2026, 1, 5, 0, 0, tzinfo=TZ)
    windows = {
        "event": WindowSpec(role="event", start=date(2026, 1, 4), end=date(2026, 1, 5)),
    }
    hours: list[dict[str, object]] = []
    for day in (date(2026, 1, 4), date(2026, 1, 5)):
        for hour in range(24):
            hours.append({"meta_ad_performance.report_date.hour": f"{day}T{hour:02d}:00:00", "net_sales": 1.0})
    port = FakePort()
    port.rows["net_sales"] = tuple(hours)
    plan = [
        QueryStep(
            step_id="s1",
            metric_id="net_sales",
            breakdowns=(),
            filters=(),
            window_role="event",
            grain="day",
            elapsed_only=True,
        )
    ]
    store = RecordingStore()

    with patch("seleric_swarm.services.elapsed.now_in", return_value=datetime(2026, 1, 5, 10, 0, tzinfo=TZ)):
        (result,) = await execute(
            plan,
            windows=windows,
            port=port,
            read_model=_read_model(),
            as_of=as_of,
            evidence_store=store,
        )

    assert result.error is None
    assert port.calls[0].granularity == "hour"
    assert len(result.rows) == 2
    assert {row.dimensions.get(ELAPSED_KEY) for row in result.rows} == {"10:00"}
    assert sorted(row.value for row in result.rows) == [10.0, 24.0]

    jan4_row = next(row for row in result.rows if row.value == 24.0)
    payload = next(payload for evidence_id, payload in store.puts if evidence_id == jan4_row.evidence_id)
    assert payload["period_start"] == "2026-01-04T00:00:00+05:30"
    assert payload["dimensions"] == {"elapsed_through": "10:00"}


@pytest.mark.asyncio
async def test_empty_plan_returns_no_results() -> None:
    results = await _run([], FakePort())
    assert results == ()

@pytest.mark.asyncio
async def test_day_grain_rows_keep_their_period() -> None:
    from seleric_swarm.plan import PERIOD_KEY

    port = FakePort()
    port.rows["net_sales"] = (
        {"channel": "paid", "v.report_date.day": "2026-01-01T00:00:00", "net_sales": 3.0},
        {"channel": "paid", "v.report_date.day": "2026-01-02T00:00:00", "net_sales": 4.0},
    )
    step = QueryStep(step_id="s1", metric_id="net_sales", breakdowns=("channel",), filters=(), window_role="event", grain="day")
    (rs,) = await _run([step], port)
    assert [(r.dimensions[PERIOD_KEY], r.value) for r in rs.rows] == [("2026-01-01", 3.0), ("2026-01-02", 4.0)]
