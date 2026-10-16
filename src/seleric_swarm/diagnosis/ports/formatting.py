"""How a value is written for a reader (percent points, multiples, currency): the rules live in the application's
metric formatting, the engine only asks."""

from __future__ import annotations

from typing import Protocol


class ValueFormatting(Protocol):
    def is_percent_share(self, metric_id: str, unit: str | None) -> bool:
        """True when the metric is a 0-1 share that readers expect as a percent."""
        ...

    def percent_points(self, value: float) -> float:
        """A canonical 0-1 share as percent points; values already on a 0-100 scale stay."""
        ...
