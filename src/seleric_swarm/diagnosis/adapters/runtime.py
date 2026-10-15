"""The live runtime: real clock, seeded streams, a cancel flag supplied by the caller."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import numpy

from seleric_swarm.diagnosis.trace.streams import stream_rng


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
        return stream_rng(self.seed, stream)

    def cancelled(self) -> bool:
        return self._is_cancelled()
