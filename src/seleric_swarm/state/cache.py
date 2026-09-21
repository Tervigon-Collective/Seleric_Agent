"""``MissionQueryCache`` (Sprint 3 Profile A).

Dedupes repeated evidence fetches within one mission — e.g. two Analytics
toolset calls both needing "net_sales, day grain, last 7 days" shouldn't
issue two ``query_metrics()`` calls against ``seleric-mcp``/Cube. Scoped to
a single mission run (one instance per ``SelericDeps``, not shared across
missions): evidence is immutable once fetched (rule 8) within a run, but a
*different* mission has its own ``as_of`` and should never see another
mission's cached fetch.

There is no real ``SemanticToolset.query_metrics()`` to wire this into yet
(Profile B, Sprint 1) — this is the cache mechanism itself, ready for that
call site once it exists.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class MissionQueryCache(Generic[K, V]):
    """Plain memoization, no TTL/eviction — a mission run is bounded
    (``ExecutionLimits``) and short-lived, so unlike ``utils/ttl_cache.py``
    (long-lived, cross-request) this never needs to expire an entry before
    the mission itself ends and the cache is discarded with it.

    ``get_or_fetch`` single-flights concurrent callers for the same key —
    two tool calls dispatched in the same LLM turn (pydantic_ai can issue
    several tool calls per turn) both see a store-miss before either has
    awaited its fetch. Without coalescing, both hit seleric-mcp/Cube
    independently and, for a live/mutating dataset, can come back with two
    different values for the identical query — live 2026-09-21: two
    ``units_sold`` fetches 3ms apart, same metric/period/dimensions,
    returned 816 and 977. That contradiction is worse than a slow cache:
    the agent has no way to know which fetch is "right" and can burn its
    remaining budget trying to reconcile them instead of answering.
    """

    def __init__(self) -> None:
        self._store: dict[K, V] = {}
        self._inflight: dict[K, asyncio.Future[V]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: K) -> V | None:
        value = self._store.get(key)
        if value is not None:
            self.hits += 1
        else:
            self.misses += 1
        return value

    def set(self, key: K, value: V) -> None:
        self._store[key] = value

    async def get_or_fetch(self, key: K, fetch: Callable[[], Awaitable[V]]) -> V:
        cached = self._store.get(key)
        if cached is not None:
            self.hits += 1
            return cached
        pending = self._inflight.get(key)
        if pending is not None:
            self.hits += 1
            return await asyncio.shield(pending)
        self.misses += 1
        future: asyncio.Future[V] = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            value = await fetch()
        except BaseException as exc:
            future.set_exception(exc)
            raise
        else:
            future.set_result(value)
            self._store[key] = value
            return value
        finally:
            del self._inflight[key]
