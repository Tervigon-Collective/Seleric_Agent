"""The event window: the event days, the history days before them and the reference days each event day is compared with
(moved from ``causal/diagnosis.py``, plan P2.2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class EventWindow:
    event_days: list[date]
    history_days: list[date]  # complete days before the event (estimation sample)
    reference: dict[date, list[date]]  # event day -> same-weekday reference days
    excluded_outliers: list[date]
    reference_kind: str
    # event day -> reference day -> level factor, taken from the OUTCOME and shared
    # by its segments and components so references stay exactly additive.
    factors: dict[date, dict[date, float]] = field(default_factory=dict)

    def factor(self, d: date, r: date) -> float:
        return self.factors.get(d, {}).get(r, 1.0)
