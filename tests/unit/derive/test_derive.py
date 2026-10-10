"""M2.3 derivation engine: every op, and every refusal."""

from __future__ import annotations

from datetime import date

import pytest

from seleric_swarm.contracts.pipeline import Derivation, Ref, ResultRow, ResultSet, WindowSpec
from seleric_swarm.derive import derive
from seleric_swarm.semantic import SemanticReadModel


def _read_model() -> SemanticReadModel:
    return SemanticReadModel.from_payload(
        {
            "metrics": [
                {"id": "sales", "aggregation": "sum", "unit": "USD"},
                {"id": "aov", "aggregation": "average", "unit": "USD"},
                {"id": "orders", "aggregation": "sum", "unit": "count"},
                {"id": "sessions", "aggregation": "sum", "unit": "count"},
                {
                    "id": "conv",
                    "aggregation": "sum",
                    "unit": None,
                    "composition": {"numerator": "orders", "denominator": "sessions"},
                },
            ]
        }
    )


def _window(start: date = date(2026, 1, 1), end: date = date(2026, 1, 31)) -> WindowSpec:
    return WindowSpec(role="event", start=start, end=end)


def _row(value: float | None, **dimensions: str) -> ResultRow:
    return ResultRow(evidence_id=f"ev-{value}", dimensions=dimensions, value=value)


def _set(
    metric_id: str,
    rows: list[ResultRow],
    *,
    unit: str | None = "USD",
    window: WindowSpec | None = None,
    breakdowns: tuple[str, ...] = (),
    ref_id: str | None = None,
) -> ResultSet:
    return ResultSet(
        ref=Ref(kind="result_set", id=ref_id or f"rs-{metric_id}"),
        step_id=f"step-{metric_id}",
        metric_id=metric_id,
        unit=unit,
        window=window or _window(),
        breakdowns=breakdowns,
        filters=(),
        rows=tuple(rows),
    )


def test_sum_adds_rows() -> None:
    result = derive(
        "sum",
        [_set("sales", [_row(10.0, channel="a"), _row(5.0, channel="b")], breakdowns=("channel",))],
        read_model=_read_model(),
    )
    assert result.error is None
    assert result.value == 15.0
    assert result.unit == "USD"


def test_sum_where_keeps_matching_rows() -> None:
    result = derive(
        "sum",
        [_set("sales", [_row(10.0, channel="a"), _row(5.0, channel="b")], breakdowns=("channel",))],
        {"where": {"channel": ["a"]}},
        read_model=_read_model(),
    )
    assert result.value == 10.0
    assert [row.dimensions["channel"] for row in result.rows] == ["a"]


def test_sum_refuses_non_additive_metric() -> None:
    result = derive("sum", [_set("aov", [_row(3.0)])], read_model=_read_model())
    assert result.error is not None
    assert result.error.code == "not_additive"
    assert result.value is None


def test_share_divides_part_by_whole() -> None:
    result = derive(
        "share",
        [
            _set("sales", [_row(25.0)], ref_id="part"),
            _set("sales", [_row(100.0)], ref_id="whole"),
        ],
        read_model=_read_model(),
    )
    assert result.value == 0.25
    assert result.unit is None


def test_share_refuses_unit_mismatch() -> None:
    result = derive(
        "share",
        [_set("sales", [_row(1.0)], unit="USD"), _set("orders", [_row(2.0)], unit="count")],
        read_model=_read_model(),
    )
    assert result.error is not None
    assert result.error.code == "unit_mismatch"


def test_share_refuses_zero_whole() -> None:
    result = derive(
        "share",
        [_set("sales", [_row(1.0)]), _set("sales", [_row(0.0)])],
        read_model=_read_model(),
    )
    assert result.error is not None
    assert result.error.code == "zero_whole"


def test_delta_subtracts() -> None:
    result = derive(
        "delta",
        [_set("sales", [_row(100.0)], ref_id="a"), _set("sales", [_row(130.0)], ref_id="b")],
        read_model=_read_model(),
    )
    assert result.value == 30.0
    assert result.unit == "USD"


def test_delta_refuses_unequal_windows() -> None:
    result = derive(
        "delta",
        [
            _set("sales", [_row(100.0)], window=_window(date(2026, 1, 1), date(2026, 1, 31))),
            _set("sales", [_row(200.0)], window=_window(date(2026, 1, 1), date(2026, 2, 28))),
        ],
        read_model=_read_model(),
    )
    assert result.error is not None
    assert result.error.code == "window_length_mismatch"


def test_delta_per_day_normalises_unequal_windows() -> None:
    result = derive(
        "delta",
        [
            _set("sales", [_row(100.0)], window=_window(date(2026, 1, 1), date(2026, 1, 31))),
            _set("sales", [_row(200.0)], window=_window(date(2026, 1, 1), date(2026, 2, 28))),
        ],
        {"per_day": True},
        read_model=_read_model(),
    )
    assert result.error is None
    assert result.value == pytest.approx(200 / 58 - 100 / 30)


def test_pct_change() -> None:
    result = derive(
        "pct_change",
        [_set("sales", [_row(100.0)]), _set("sales", [_row(150.0)])],
        read_model=_read_model(),
    )
    assert result.value == pytest.approx(0.5)
    assert result.unit is None


def test_pct_change_refuses_zero_base() -> None:
    result = derive(
        "pct_change",
        [_set("sales", [_row(0.0)]), _set("sales", [_row(150.0)])],
        read_model=_read_model(),
    )
    assert result.error is not None
    assert result.error.code == "zero_base"


def test_per_day_divides_by_window_days() -> None:
    result = derive(
        "per_day",
        [_set("sales", [_row(290.0)], window=_window(date(2026, 1, 1), date(2026, 1, 30)))],
        read_model=_read_model(),
    )
    assert result.value == pytest.approx(10.0)
    assert result.unit == "USD/day"


def test_per_day_refuses_without_window() -> None:
    derivation = Derivation(
        ref=Ref(kind="derivation", id="d1"),
        op="delta",
        inputs=(),
        params={},
        value=10.0,
        unit="USD",
    )
    result = derive("per_day", [derivation], read_model=_read_model())
    assert result.error is not None
    assert result.error.code == "no_window"


def test_ratio() -> None:
    result = derive(
        "ratio",
        [
            _set("orders", [_row(30.0)], unit="count", ref_id="num"),
            _set("sessions", [_row(300.0)], unit="count", ref_id="den"),
        ],
        read_model=_read_model(),
    )
    assert result.value == pytest.approx(0.1)


def test_ratio_refuses_metric_with_composition() -> None:
    result = derive(
        "ratio",
        [
            _set("conv", [_row(0.1)], unit=None, ref_id="num"),
            _set("sessions", [_row(1.0)], unit="count", ref_id="den"),
        ],
        read_model=_read_model(),
    )
    assert result.error is not None
    assert result.error.code == "composition_defined"


def test_ratio_refuses_zero_denominator() -> None:
    result = derive(
        "ratio",
        [
            _set("orders", [_row(1.0)], unit="count"),
            _set("sessions", [_row(0.0)], unit="count"),
        ],
        read_model=_read_model(),
    )
    assert result.error is not None
    assert result.error.code == "zero_denominator"


def test_recompute_reads_composition() -> None:
    result = derive(
        "recompute",
        [
            _set("orders", [_row(30.0)], unit="count"),
            _set("sessions", [_row(300.0)], unit="count"),
        ],
        {"metric_id": "conv"},
        read_model=_read_model(),
    )
    assert result.error is None
    assert result.value == pytest.approx(0.1)
    assert result.op == "recompute"


def test_recompute_refuses_missing_component() -> None:
    result = derive(
        "recompute",
        [_set("orders", [_row(30.0)], unit="count")],
        {"metric_id": "conv"},
        read_model=_read_model(),
    )
    assert result.error is not None
    assert result.error.code == "missing_components"


def test_recompute_refuses_metric_without_composition() -> None:
    result = derive("recompute", [], {"metric_id": "sales"}, read_model=_read_model())
    assert result.error is not None
    assert result.error.code == "no_composition"


def test_reconcile_returns_residual_and_explained_share() -> None:
    result = derive(
        "reconcile",
        [
            _set("sales", [_row(30.0)], ref_id="p1"),
            _set("sales", [_row(60.0)], ref_id="p2"),
            _set("sales", [_row(100.0)], ref_id="total"),
        ],
        read_model=_read_model(),
    )
    assert result.value == pytest.approx(10.0)
    assert result.params["residual"] == pytest.approx(10.0)
    assert result.params["explained_share"] == pytest.approx(0.9)
    assert len(result.rows) == 2


def test_rank_top_two_descending() -> None:
    result = derive(
        "rank",
        [
            _set(
                "sales",
                [_row(1.0, channel="a"), _row(9.0, channel="b"), _row(5.0, channel="c")],
                breakdowns=("channel",),
            )
        ],
        {"by": "value", "order": "desc", "limit": 2},
        read_model=_read_model(),
    )
    assert [row.dimensions["channel"] for row in result.rows] == ["b", "c"]


def test_rank_refuses_bad_limit() -> None:
    result = derive(
        "rank",
        [_set("sales", [_row(1.0, channel="a")], breakdowns=("channel",))],
        {"limit": 0},
        read_model=_read_model(),
    )
    assert result.error is not None
    assert result.error.code == "bad_limit"


def test_join_keeps_rows_with_shared_keys() -> None:
    result = derive(
        "join",
        [
            _set("sales", [_row(1.0, channel="a"), _row(2.0, channel="b")], breakdowns=("channel",)),
            _set("orders", [_row(3.0, channel="a")], unit="count", breakdowns=("channel",)),
        ],
        {"on": ["channel"]},
        read_model=_read_model(),
    )
    assert len(result.rows) == 1
    assert result.rows[0].dimensions["channel"] == "a"


def test_join_refuses_keys_not_carried() -> None:
    result = derive(
        "join",
        [
            _set("sales", [_row(1.0, channel="a")], breakdowns=("channel",)),
            _set("orders", [_row(3.0, region="x")], unit="count", breakdowns=("region",)),
        ],
        {"on": ["channel"]},
        read_model=_read_model(),
    )
    assert result.error is not None
    assert result.error.code == "keys_not_carried"


def test_derivation_cites_input_refs_and_is_deterministic() -> None:
    inputs = [
        _set("sales", [_row(1.0)], ref_id="a"),
        _set("sales", [_row(2.0)], ref_id="b"),
    ]
    first = derive("delta", inputs, read_model=_read_model())
    second = derive("delta", inputs, read_model=_read_model())
    assert [item.id for item in first.inputs] == ["a", "b"]
    assert first.ref.kind == "derivation"
    assert first.ref.id == second.ref.id


def test_unknown_op_raises() -> None:
    with pytest.raises(ValueError, match="unknown derive op"):
        derive("nope", [], read_model=_read_model())


def test_public_interface_is_only_derive() -> None:
    import seleric_swarm.derive as package

    assert package.__all__ == ["derive"]
