"""Per-model health gate for the LLM gateway.

Opens after ``failure_threshold`` consecutive failures on a model, stays open
for ``cooldown_s``, then half-opens to let one probe request through. A
successful probe closes it; a failed probe re-opens it and restarts the
cooldown.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    cooldown_s: float = 30.0
    _consecutive_failures: int = field(default=0, init=False, repr=False)
    _opened_at: float | None = field(default=None, init=False, repr=False)

    def allow(self) -> bool:
        if self._opened_at is None:
            return True
        return time.monotonic() - self._opened_at >= self.cooldown_s

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._opened_at = time.monotonic()

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if time.monotonic() - self._opened_at >= self.cooldown_s:
            return "half_open"
        return "open"
