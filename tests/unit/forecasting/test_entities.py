"""Entity selection and sparse-entity refusal."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pytest

from seleric_swarm.forecasting.entities import (
    entity_filter,
    select_entities,
    sum_mismatch_warning,
)
from seleric_swarm.forecasting.policies import load_forecast_policies
from seleric_swarm.forecasting.quality import assess_series


@pytest.mark.asyncio
async def test_select_entities_top_n_by_volume():
    policies = load_forecast_policies()
    rows = []
    for ent, vol in [("meta", 100.0), ("google", 80.0), ("organic", 5.0), ("tiny", 1.0)]:
        for i in range(10):
            rows.append(
                {
                    "orders.day": f"2026-09-{i + 1:02d}",
                    "lt_platform": ent,
                    "net_sales": vol,
                }
            )
    mcp = AsyncMock()
    mcp.call = AsyncMock(return_value={"rows": rows, "provenance": {}})

    sel = await select_entities(
        mcp_client=mcp,
        metric_id="net_sales",
        dimension="lt_platform",
        as_of=date(2026, 10, 9),
        policies=policies,
    )
    assert "meta" in sel.values and "google" in sel.values
    assert "tiny" not in sel.values  # below min_volume_share
    assert any("Q_SPARSE" in r[1] or "sparse" in r[1].lower() or "share" in r[1] for r in sel.refused) or "tiny" in {
        r[0] for r in sel.refused
    }


@pytest.mark.asyncio
async def test_named_entities_forecast_despite_low_share():
    rows = [
        {"orders.day": f"2026-09-{i + 1:02d}", "lt_platform": ent, "net_sales": vol}
        for ent, vol in [("meta", 1000.0), ("tiny", 1.0)]
        for i in range(10)
    ]
    mcp = AsyncMock()
    mcp.call = AsyncMock(return_value={"rows": rows, "provenance": {}})
    sel = await select_entities(
        mcp_client=mcp,
        metric_id="net_sales",
        dimension="lt_platform",
        as_of=date(2026, 10, 9),
        policies=load_forecast_policies(),
        requested_values=["tiny", "ghost"],
    )
    assert sel.values == ["tiny"]  # named + has volume; "ghost" has none
    assert [r[0] for r in sel.refused] == ["ghost"]


@pytest.mark.asyncio
async def test_select_entities_unlisted_dimension_defers_to_catalogue():
    """Whether a metric can be sliced is the catalogue's call; the policy list only tunes limits."""
    policies = load_forecast_policies()
    mcp = AsyncMock()
    sel = await select_entities(
        mcp_client=mcp,
        metric_id="net_sales",
        dimension="not_a_real_dim",
        as_of=date(2026, 10, 9),
        policies=policies,
    )
    assert sel.values == []
    assert "T_ENTITY_UNSUPPORTED" not in sel.warnings
    assert sel.policy is not None


def test_entity_filter_shape():
    assert entity_filter("lt_platform", "meta") == [
        {"dimension": "lt_platform", "operator": "equals", "values": ["meta"]}
    ]


def test_sum_mismatch_warning():
    assert sum_mismatch_warning(100.0, [48.0, 50.0]) is None  # 2% gap
    warn = sum_mismatch_warning(100.0, [40.0, 40.0])  # 20% gap
    assert warn is not None and "mismatch" in warn


def test_q_sparse_entity_gate():
    from datetime import timedelta

    idx = [date(2026, 1, 1) + timedelta(days=i) for i in range(30)]
    values = [0.0] * 20 + [1.0] * 10
    _, verdicts = assess_series(
        "net_sales[tiny]",
        values,
        idx,
        role="target",
        min_history_days=10,
        min_volume_share=0.05,
        account_total=1000.0,
    )
    assert any(v.code == "Q_SPARSE_ENTITY" and v.action == "refuse" for v in verdicts)


@pytest.mark.asyncio
async def test_named_entity_resolves_partial_and_recased_names():
    rows = [
        {"orders.day": f"2026-09-{i + 1:02d}", "product_title": t, "net_sales": v}
        for t, v in [("Acme Widget Pro", 50.0), ("Zenith Gadget", 80.0)]
        for i in range(10)
    ]
    mcp = AsyncMock()
    mcp.call = AsyncMock(return_value={"rows": rows, "provenance": {}})
    sel = await select_entities(
        mcp_client=mcp, metric_id="net_sales", dimension="product_title",
        as_of=date(2026, 10, 9), policies=load_forecast_policies(),
        requested_values=["widget pro", "ZENITH gadget"],
    )
    assert sel.values == ["Acme Widget Pro", "Zenith Gadget"]
    assert not sel.refused


def test_breakdown_read_through_conformed_sibling_column():
    from seleric_swarm.forecasting.entities import _breakdown_value

    row = {"order_date.day": "2026-10-01T00:00:00.000", "sibling_title": "Acme Widget Pro", "gross_sales": "5"}
    assert _breakdown_value(row, "product_title", "gross_sales") == "Acme Widget Pro"
    assert _breakdown_value({"product_title": "X", "gross_sales": "1"}, "product_title", "gross_sales") == "X"
