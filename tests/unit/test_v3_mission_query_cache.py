from __future__ import annotations

import pytest

from seleric_swarm.state.cache import MissionQueryCache


def test_get_set_roundtrip_and_hit_miss_counters() -> None:
    cache: MissionQueryCache[str, int] = MissionQueryCache()
    assert cache.get("k") is None
    assert cache.misses == 1
    cache.set("k", 1)
    assert cache.get("k") == 1
    assert cache.hits == 1


@pytest.mark.asyncio
async def test_get_or_fetch_dedupes_repeated_calls() -> None:
    cache: MissionQueryCache[str, int] = MissionQueryCache()
    calls = 0

    async def fetch() -> int:
        nonlocal calls
        calls += 1
        return 42

    first = await cache.get_or_fetch("metric.net_sales|day|2026-09-17", fetch)
    second = await cache.get_or_fetch("metric.net_sales|day|2026-09-17", fetch)
    assert first == second == 42
    assert calls == 1
    assert cache.hits == 1
    assert cache.misses == 1


@pytest.mark.asyncio
async def test_get_or_fetch_distinct_keys_both_fetch() -> None:
    cache: MissionQueryCache[str, int] = MissionQueryCache()
    calls = 0

    async def fetch() -> int:
        nonlocal calls
        calls += 1
        return calls

    a = await cache.get_or_fetch("a", fetch)
    b = await cache.get_or_fetch("b", fetch)
    assert (a, b) == (1, 2)
    assert cache.misses == 2
    assert cache.hits == 0
