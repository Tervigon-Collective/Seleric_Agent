"""Unit tests for CatalogueBootstrap (services/catalogue_bootstrap.py).

Covers:
  1. warm() populates the cache from catalogue_bootstrap (then listing fallbacks).
  2. has() returns True only for known IDs.
  3. refresh_if_stale() only calls warm() when TTL is exceeded.
  4. warm() failure is non-fatal — cache stays empty, is_warm() is False.
  5. unresolvable() returns IDs absent from the live cache.

All tests use a trivial mock instead of a full MCPGateway to stay fast and
isolated; no network, no real MCP server.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from seleric_swarm.services.catalogue_bootstrap import CatalogueBootstrap, CatalogueMetricMeta


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_MATCHES = [
    {"id": "session_conversion_rate", "label": "Session CVR", "view": "session_funnel"},
    {"id": "web_sessions", "label": "Web Sessions", "view": "session_funnel"},
    {"id": "total_ad_spend", "label": "Total Ad Spend", "view": "canonical_pnl"},
    {"id": "cac", "label": "CAC", "view": "ltv_cac"},
]


def _mock_mcp(matches: list[dict] | None = None, raises: Exception | None = None) -> MagicMock:
    """Return a mock MCPGateway whose ``call`` coroutine returns ``matches``."""
    mcp = MagicMock()
    if raises is not None:
        mcp.call = AsyncMock(side_effect=raises)
    else:
        mcp.call = AsyncMock(return_value={"matches": matches if matches is not None else _SAMPLE_MATCHES})
    return mcp


# ---------------------------------------------------------------------------
# 1. warm() populates the cache
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_warm_populates_cache():
    """warm() prefers catalogue_bootstrap and fills the cache by metric id."""
    mcp = _mock_mcp()
    bootstrap = CatalogueBootstrap(mcp, agent_id="coordinator_agent")

    assert not bootstrap.is_warm()
    count = await bootstrap.warm()

    assert count == len(_SAMPLE_MATCHES)
    assert bootstrap.is_warm()
    for m in _SAMPLE_MATCHES:
        assert bootstrap.has(m["id"]), f"Expected '{m['id']}' in cache"
    cvr = bootstrap.get("session_conversion_rate")
    assert isinstance(cvr, CatalogueMetricMeta)
    assert cvr.label == "Session CVR"
    assert cvr.view == "session_funnel"
    mcp.call.assert_awaited_once_with(
        agent_id="coordinator_agent",
        capability="seleric.catalogue_bootstrap",
        arguments={},
    )


# ---------------------------------------------------------------------------
# 2. has() returns True only for IDs in the cache
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_has_returns_true_for_known_id_and_false_for_unknown():
    """has() must distinguish between IDs returned by the catalogue and
    IDs that were never in the response."""
    bootstrap = CatalogueBootstrap(_mock_mcp(), agent_id="coordinator_agent")
    await bootstrap.warm()

    assert bootstrap.has("web_sessions")
    assert bootstrap.has("cac")
    assert not bootstrap.has("session_purchase_rate")   # old stale ID
    assert not bootstrap.has("ghost_metric_xyz")        # never existed
    assert not bootstrap.has("")                        # empty string guard


# ---------------------------------------------------------------------------
# 3. refresh_if_stale() only calls warm() when TTL is exceeded
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refresh_if_stale_only_calls_warm_when_ttl_exceeded():
    """After an initial warm(), refresh_if_stale() must not call warm() again
    until the TTL elapses.  With a large TTL (24 h) the second call must be
    a no-op; with a TTL of 0 it must re-warm every time."""
    # Large TTL — second refresh_if_stale() must not re-warm.
    mcp_large = _mock_mcp()
    bs_large = CatalogueBootstrap(mcp_large, ttl_seconds=86400)
    await bs_large.refresh_if_stale()       # first call → warms
    await bs_large.refresh_if_stale()       # second call → TTL not elapsed
    assert mcp_large.call.await_count == 1, "should not re-warm before TTL"

    # TTL = 0 — every refresh_if_stale() must re-warm.
    mcp_zero = _mock_mcp()
    bs_zero = CatalogueBootstrap(mcp_zero, ttl_seconds=0)
    await bs_zero.refresh_if_stale()        # first call → warms
    # Force monotonic clock to advance by patching _warmed_at to the past.
    bs_zero._warmed_at = time.monotonic() - 1.0  # 1 second ago > ttl=0
    await bs_zero.refresh_if_stale()        # second call → should_refresh() is True
    assert mcp_zero.call.await_count == 2, "should re-warm after TTL=0"


# ---------------------------------------------------------------------------
# 4. warm() failure is non-fatal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_warm_failure_is_nonfatal():
    """When the MCP call raises, warm() must not propagate the exception.
    The cache must remain empty and is_warm() must return False so
    _resolve_measure falls through to Steps 1+2 unchanged."""
    mcp = _mock_mcp(raises=RuntimeError("MCP timeout"))
    bootstrap = CatalogueBootstrap(mcp)

    count = await bootstrap.warm()

    assert count == 0
    assert not bootstrap.is_warm()          # cache empty
    assert bootstrap.known_ids() == set()   # nothing stored
    # is_warm() → False prevents any incorrect Step 0 "hit"
    assert not bootstrap.has("session_conversion_rate")


# ---------------------------------------------------------------------------
# 5. unresolvable() returns IDs absent from the live cache
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unresolvable_finds_missing_ids():
    """unresolvable() must return only those candidate IDs that are NOT
    present in the live cache — the operator can update metric_registry.yaml
    for each returned entry."""
    bootstrap = CatalogueBootstrap(_mock_mcp())
    await bootstrap.warm()

    # "session_purchase_rate" is NOT in _SAMPLE_MATCHES — stale.
    # "web_sessions" IS — current.
    stale = bootstrap.unresolvable([
        "session_conversion_rate",  # ✓ in cache
        "web_sessions",             # ✓ in cache
        "session_purchase_rate",    # ✗ stale
        "return_rate",              # ✗ stale
        "",                         # empty string — filtered out
    ])
    assert set(stale) == {"session_purchase_rate", "return_rate"}
    # Empty string should NOT appear — unresolvable skips falsy entries.
    assert "" not in stale


# ---------------------------------------------------------------------------
# 6. startup staleness warning (registry_hints param of warm())
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_warm_with_registry_hints_logs_stale_entries(caplog):
    """When registry_hints are passed, warm() must log a WARNING for each
    hint ID that is absent from the live catalogue.  Entries that ARE
    present must not be logged."""
    import logging
    bootstrap = CatalogueBootstrap(_mock_mcp())

    hints = [
        "session_conversion_rate",   # ✓ live → no warning
        "session_purchase_rate",     # ✗ stale → WARNING
        "total_ad_spend",            # ✓ live → no warning
        "old_return_rate",           # ✗ stale → WARNING
    ]
    with caplog.at_level(logging.WARNING, logger="seleric_swarm.services.catalogue_bootstrap"):
        await bootstrap.warm(registry_hints=hints)

    warning_texts = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("session_purchase_rate" in t for t in warning_texts), \
        "Expected warning for session_purchase_rate"
    assert any("old_return_rate" in t for t in warning_texts), \
        "Expected warning for old_return_rate"
    assert not any("session_conversion_rate" in t for t in warning_texts), \
        "session_conversion_rate is live — must not be warned"
    assert not any("total_ad_spend" in t for t in warning_texts), \
        "total_ad_spend is live — must not be warned"


@pytest.mark.asyncio
async def test_warm_falls_back_to_empty_search_when_bootstrap_empty():
    """Old servers without catalogue_bootstrap still warm via search("")."""

    async def call(*, agent_id, capability, arguments):
        del agent_id, arguments
        if capability == "seleric.catalogue_search_metrics":
            return {"matches": _SAMPLE_MATCHES}
        return {"metrics": []}

    mcp = MagicMock()
    mcp.call = AsyncMock(side_effect=call)
    bootstrap = CatalogueBootstrap(mcp, agent_id="coordinator_agent")
    count = await bootstrap.warm()
    assert count == len(_SAMPLE_MATCHES)
    assert bootstrap.is_warm()
    caps = [c.kwargs["capability"] for c in mcp.call.await_args_list]
    assert caps == [
        "seleric.catalogue_bootstrap",
        "seleric.catalogue_list_metrics",
        "seleric.catalogue_search_metrics",
    ]


@pytest.mark.asyncio
async def test_warm_loads_dimensions_and_grain_defaults():
    payload = {
        "metrics": _SAMPLE_MATCHES,
        "dimensions": [{"id": "channel", "aliases": ["marketplace"]}],
        "grain_defaults": {"channel": {"dimension": "channel", "metric": "channel_orders"}},
    }
    mcp = _mock_mcp()
    mcp.call = AsyncMock(return_value=payload)
    bootstrap = CatalogueBootstrap(mcp)
    await bootstrap.warm()
    assert "channel" in bootstrap.dimension_ids()
    assert bootstrap.alias_index()["marketplace"] == "channel"
    assert bootstrap.grain_defaults()["channel"]["metric"] == "channel_orders"
