"""Where the per-driver causal estimates are computed (identify, fit, refute): in worker processes in production,
in-process or from a recording elsewhere. One result per job, in order."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from seleric_swarm.diagnosis.domain.estimates import TimeSeriesEffect


class EffectEstimator(Protocol):
    def estimate_many(self, jobs: Sequence[Mapping[str, Any]]) -> list[TimeSeriesEffect]: ...
