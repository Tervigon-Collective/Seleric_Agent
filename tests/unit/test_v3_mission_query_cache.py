from __future__ import annotations

import asyncio

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


@pytest.mark.asyncio
async def test_concurrent_calls_for_the_same_key_single_flight_to_one_fetch() -> None:
    """Live incident: two ``units_sold`` tool calls dispatched in the same
    LLM turn, same metric/period, 3ms apart — both saw a cache miss and
    both hit Cube independently, coming back with different values (816 vs
    977) for what should have been one fetch. Concurrent callers must share
    the in-flight fetch, not each start their own."""
    cache: MissionQueryCache[str, int] = MissionQueryCache()
    calls = 0
    started = asyncio.Event()

    async def fetch() -> int:
        nonlocal calls
        calls += 1
        started.set()
        await asyncio.sleep(0.02)
        return 42

    results = await asyncio.gather(
        cache.get_or_fetch("k", fetch),
        cache.get_or_fetch("k", fetch),
    )
    assert results == [42, 42]
    assert calls == 1
    assert cache.misses == 1
    assert cache.hits == 1


@pytest.mark.asyncio
async def test_concurrent_calls_propagate_the_same_failure_without_deadlock() -> None:
    cache: MissionQueryCache[str, Exception] = MissionQueryCache()
    calls = 0

    async def fetch() -> int:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        raise ValueError("mcp unavailable")

    results = await asyncio.gather(
        cache.get_or_fetch("k", fetch),
        cache.get_or_fetch("k", fetch),
        return_exceptions=True,
    )
    assert calls == 1
    assert all(isinstance(r, ValueError) for r in results)


@pytest.mark.asyncio
async def test_uncacheable_result_is_refetched() -> None:
    """A failure returned as a value (call_metrics_query's {"error": ...}) must
    not be replayed for the rest of the mission -- a retry has to re-fetch."""
    cache: MissionQueryCache[str, dict] = MissionQueryCache()
    results = iter([{"error": "ConnectError: boom"}, {"rows": [1]}])

    async def fetch() -> dict:
        return next(results)

    ok = lambda r: not r.get("error")  # noqa: E731
    assert (await cache.get_or_fetch("k", fetch, cacheable=ok))["error"]
    assert await cache.get_or_fetch("k", fetch, cacheable=ok) == {"rows": [1]}
    assert cache.peek("k") == {"rows": [1]}


@pytest.mark.asyncio
async def test_waiter_survives_owner_timeout() -> None:
    """Two identical queries in one turn: when the first caller's wait_for
    times out, the second must not inherit its CancelledError (which aborts
    the tool call) -- it takes the fetch over and gets the value."""
    cache: MissionQueryCache[str, int] = MissionQueryCache()

    async def slow() -> int:
        await asyncio.sleep(0.2)
        return 7

    async def owner() -> int:
        return await asyncio.wait_for(cache.get_or_fetch("k", slow), 0.05)

    async def waiter() -> int:
        await asyncio.sleep(0.01)
        return await asyncio.wait_for(cache.get_or_fetch("k", slow), 2)

    first, second = await asyncio.gather(owner(), waiter(), return_exceptions=True)
    assert isinstance(first, TimeoutError)
    assert second == 7
