"""``EffectEstimator`` over the existing worker-process pool (``causal/estimate_pool.py``)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from seleric_swarm.causal import estimate_pool
from seleric_swarm.diagnosis.domain.estimates import TimeSeriesEffect


class PoolEstimator:
    def estimate_many(self, jobs: Sequence[Mapping[str, Any]]) -> list[TimeSeriesEffect]:
        return estimate_pool.estimate_many([dict(job) for job in jobs])
