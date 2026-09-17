"""Cooperative cancellation backends."""

from __future__ import annotations

from threading import RLock
from typing import Any, Protocol


class CancellationBackend(Protocol):
    def request(self, mission_id: str) -> None: ...
    def is_requested(self, mission_id: str) -> bool: ...
    def clear(self, mission_id: str) -> None: ...


class InMemoryCancellationBackend:
    def __init__(self) -> None:
        self._requested: set[str] = set()
        self._lock = RLock()

    def request(self, mission_id: str) -> None:
        with self._lock:
            self._requested.add(mission_id)

    def is_requested(self, mission_id: str) -> bool:
        with self._lock:
            return mission_id in self._requested

    def clear(self, mission_id: str) -> None:
        with self._lock:
            self._requested.discard(mission_id)


class RedisCancellationBackend:
    """Redis 5.x compatible cancellation flag adapter."""

    def __init__(self, client: Any, *, ttl_s: int = 86_400, prefix: str = "seleric:cancel:") -> None:
        self._client = client
        self._ttl_s = ttl_s
        self._prefix = prefix

    def _key(self, mission_id: str) -> str:
        return f"{self._prefix}{mission_id}"

    def request(self, mission_id: str) -> None:
        self._client.set(self._key(mission_id), "1", ex=self._ttl_s)

    def is_requested(self, mission_id: str) -> bool:
        return bool(self._client.get(self._key(mission_id)))

    def clear(self, mission_id: str) -> None:
        self._client.delete(self._key(mission_id))


def build_cancellation_backend(backend: str, redis_url: str) -> CancellationBackend:
    if backend == "redis":
        if not redis_url.strip():
            raise ValueError("cancellation_backend=redis requires redis_url")
        try:
            import redis
        except ImportError as exc:
            raise RuntimeError("cancellation_backend=redis requires redis>=5") from exc
        return RedisCancellationBackend(redis.Redis.from_url(redis_url))
    return InMemoryCancellationBackend()


class MissionCancelledError(RuntimeError):
    pass
