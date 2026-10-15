"""Where daily metric series come from."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from seleric_swarm.diagnosis.domain.types import SeriesRequest, SeriesResult


class SeriesPort(Protocol):
    async def fetch(self, requests: Sequence[SeriesRequest]) -> list[SeriesResult]:
        """One result per request, in order. A source failure is carried in ``SeriesResult.error``.

        Takes a batch because the live source answers many metrics of one view in a single query."""
        ...
