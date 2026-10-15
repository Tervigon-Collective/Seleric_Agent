"""The live runtime: real clock, seeded streams, a cancel flag supplied by the caller."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime

import numpy


def _stream_key(stream: str) -> int:
    # Not ``hash()``: Python salts string hashes per process, which would break replay.
    return int.from_bytes(hashlib.sha256(stream.encode("utf-8")).digest()[:8], "big")


class SeededRuntime:
    def __init__(
        self,
        seed: int,
        *,
        clock: Callable[[], datetime] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self.seed = seed
        self._clock = clock or (lambda: datetime.now(UTC))
        self._is_cancelled = is_cancelled or (lambda: False)

    def now(self) -> datetime:
        return self._clock()

    def rng(self, stream: str) -> numpy.random.Generator:
        return numpy.random.default_rng([self.seed, _stream_key(stream)])

    def cancelled(self) -> bool:
        return self._is_cancelled()
