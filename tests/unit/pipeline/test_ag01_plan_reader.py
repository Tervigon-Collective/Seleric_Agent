"""AG-01 evidence-safe PlanReader: totals and breakdowns must not invent numbers."""

from __future__ import annotations

from datetime import date, datetime, UTC

from seleric_swarm.contracts.pipeline import (
    FilterSpec,
    Ref,
    ResultSet,
    ResultRow,
    WindowSpec,
)
from seleric_swarm.pipeline._run_plan import PlanReader
from seleric_swarm.semantic import SemanticReadModel

MODEL = SemanticReadModel.from_payload({
    "catalogue_version": "zz-v1",
    "metrics": [
        {"id": "zz_add", "aggregation": "additive"},
        {"id": "zz_avg", "aggregation": "ratio"},
    ],
})
WINDOW = WindowSpec(role="event", start=date(2026, 10, 9), end=date(2026, 10, 9))
AS_OF = datetime(2026, 10, 10, tzinfo=UTC)


class FakeReader(PlanReader):
    """Serve fixed rows without the semantic port."""

    def __init__(self, rows, *, breakdown_dimension: str = "zz_zone") -> None:
        super().__init__(None, MODEL, AS_OF)
        self._fixed = rows
        self._breakdown_dimension = breakdown_dimension

    async def _rows(self, metric_id, window, breakdowns, filters):
        if breakdowns:
            return ResultSet(
                ref=Ref(kind="result_set", id="x"), step_id="x", metric_id=metric_id, unit="INR",
                window=window, breakdowns=breakdowns, filters=filters, rows=tuple(
                    ResultRow(evidence_id=f"e{i}", dimensions={self._breakdown_dimension: k}, value=v)
                    for i, (k, v) in enumerate(self._fixed)
                ),
            )
        return ResultSet(
            ref=Ref(kind="result_set", id="x"), step_id="x", metric_id=metric_id, unit="INR",
            window=window, breakdowns=(), filters=filters, rows=tuple(
                ResultRow(evidence_id=f"e{i}", dimensions={}, value=v) for i, v in enumerate(self._fixed)
            ),
        )


async def test_ag01_t01_total_single_row_is_exact() -> None:
    assert await FakeReader([97480.9614324181]).total("zz_add", WINDOW, ()) == 97480.9614324181


async def test_ag01_t02_total_multi_row_additive_sums() -> None:
    assert await FakeReader([100.0, 200.0, 300.0]).total("zz_add", WINDOW, ()) == 600.0


async def test_ag01_t03_total_multi_row_non_additive_refuses() -> None:
    # Summing averages would invent a number that was never read.
    assert await FakeReader([1.5, 2.5]).total("zz_avg", WINDOW, ()) is None


async def test_ag01_t04_total_no_rows_is_unknown_not_zero() -> None:
    assert await FakeReader([]).total("zz_add", WINDOW, ()) is None


async def test_ag01_t05_total_single_row_non_additive_is_exact() -> None:
    assert await FakeReader([2.5]).total("zz_avg", WINDOW, ()) == 2.5


async def test_ag01_t06_breakdown_additive_duplicate_keys_sum() -> None:
    out = await FakeReader([("north", 10.0), ("north", 20.0), ("south", 5.0)]).breakdown(
        "zz_add", WINDOW, "zz_zone", ()
    )
    assert out == {"north": 30.0, "south": 5.0}


async def test_ag01_t07_breakdown_non_additive_duplicate_key_dropped() -> None:
    out = await FakeReader([("north", 1.5), ("north", 2.5), ("south", 3.0)]).breakdown(
        "zz_avg", WINDOW, "zz_zone", ()
    )
    assert "north" not in out and out.get("south") == 3.0


async def test_ag01_t08_breakdown_non_additive_single_rows_kept() -> None:
    out = await FakeReader([("north", 1.5), ("south", 2.5)]).breakdown("zz_avg", WINDOW, "zz_zone", ())
    assert out == {"north": 1.5, "south": 2.5}


async def test_ag01_t09_breakdown_empty_rows_is_empty() -> None:
    assert await FakeReader([]).breakdown("zz_add", WINDOW, "zz_zone", ()) == {}


async def test_ag01_t10_total_skips_null_values() -> None:
    assert await FakeReader([None, 5.0]).total("zz_add", WINDOW, ()) == 5.0


async def test_ag01_t11_total_all_null_is_unknown() -> None:
    assert await FakeReader([None, None]).total("zz_add", WINDOW, ()) is None


async def test_ag01_t12_filters_reach_the_reader() -> None:
    seen = {}

    class Recording(FakeReader):
        async def _rows(self, metric_id, window, breakdowns, filters):
            seen["filters"] = filters
            return await super()._rows(metric_id, window, breakdowns, filters)

    f = FilterSpec(dimension="zz_zone", operator="in", values=("north",))
    await Recording([1.0]).total("zz_add", WINDOW, (f,))
    assert seen["filters"] == (f,)
