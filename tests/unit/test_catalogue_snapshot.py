"""Unit tests for the whole-catalogue-in-agent-memory cache.

Covers CatalogueSnapshot rendering / id lookup / closest-id suggestions,
CatalogueBootstrap.snapshot(), and that the mission prompt embeds the full
listing so the agent resolves against the catalogue instead of a Qdrant
top-k guess.
"""

from __future__ import annotations

from datetime import UTC, datetime

from seleric_swarm.agent import runner
from seleric_swarm.services.catalogue_bootstrap import (
    CatalogueBootstrap,
    CatalogueMetricMeta,
    CatalogueSnapshot,
)


def _snapshot() -> CatalogueSnapshot:
    return CatalogueSnapshot(
        metrics=(
            CatalogueMetricMeta(
                id="net_sales",
                label="Net Sales",
                supported_dimensions=["brand", "channel"],
                raw={"unit": "INR", "aliases": ["ns", "np"]},
            ),
            CatalogueMetricMeta(id="total_ad_spend", label="Ad Spend", raw={"unit": "INR"}),
        ),
        dimensions=("brand", "channel"),
    )


def test_render_lists_every_metric_with_aliases_and_dims() -> None:
    rendered = _snapshot().render()
    assert "net_sales: Net Sales" in rendered
    assert "aliases=ns, np" in rendered
    assert "dims=brand, channel" in rendered
    assert "total_ad_spend" in rendered
    assert "Dimensions: brand, channel" in rendered


def test_empty_snapshot_renders_nothing() -> None:
    assert CatalogueSnapshot().render() == ""


def test_has_metric_and_ids() -> None:
    snap = _snapshot()
    assert snap.has_metric("net_sales")
    assert not snap.has_metric("nope")
    assert snap.metric_ids() == frozenset({"net_sales", "total_ad_spend"})


def test_closest_metric_ids_maps_alias_to_id() -> None:
    snap = _snapshot()
    # An alias typo should resolve back to the canonical id, not the alias.
    assert "net_sales" in snap.closest_metric_ids("ns")
    assert "net_sales" in snap.closest_metric_ids("net_sale")


def test_bootstrap_snapshot_reflects_cache() -> None:
    bootstrap = CatalogueBootstrap(mcp=None)  # no MCP call in this test
    bootstrap._load_payload(
        {
            "metrics": [
                {"id": "net_sales", "display_name": "Net Sales", "supported_dimensions": ["brand"]}
            ],
            "dimensions": [{"id": "brand", "aliases": ["marca"]}],
        }
    )
    snap = bootstrap.snapshot()
    assert snap.has_metric("net_sales")
    assert "brand" in snap.dimensions


def test_mission_prompt_embeds_catalogue() -> None:
    prompt = runner._mission_prompt(
        "what is net sales",
        datetime(2026, 9, 18, tzinfo=UTC),
        "Asia/Kolkata",
        catalogue=_snapshot(),
    )
    assert "[catalogue]" in prompt
    assert "net_sales: Net Sales" in prompt


def test_mission_prompt_without_catalogue_has_no_block() -> None:
    prompt = runner._mission_prompt(
        "hi", datetime(2026, 9, 18, tzinfo=UTC), "Asia/Kolkata"
    )
    assert "[catalogue]" not in prompt
