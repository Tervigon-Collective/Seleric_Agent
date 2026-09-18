"""Small in-process TTL+LRU cache for expensive, repeatable LLM calls.

Not a distributed/shared cache -- each process gets its own, which is enough
to stop the same repeated query (a user re-asking, a retry, a flaky client)
from paying for another LLM round trip within the TTL window.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    def __init__(self, *, maxsize: int = 256, ttl_s: float = 300.0) -> None:
        self._maxsize = maxsize
        self._ttl_s = ttl_s
        self._store: OrderedDict[K, tuple[float, V]] = OrderedDict()

    def get(self, key: K) -> V | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < time.monotonic():
            del self._store[key]
            return None
        self._store.move_to_end(key)
        return value

    def set(self, key: K, value: V) -> None:
        self._store[key] = (time.monotonic() + self._ttl_s, value)
        self._store.move_to_end(key)
        while len(self._store) > self._maxsize:
            self._store.popitem(last=False)

    def clear(self) -> None:
        self._store.clear()
