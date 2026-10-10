"""Calendar events (festivals, paydays, month ends) from the injected calendar config."""

from __future__ import annotations

from datetime import date, timedelta

from seleric_swarm.context.model import ContextEvent, ContextWindow
from seleric_swarm.forecasting.calendar import CalendarConfig, is_month_end, is_payday
from seleric_swarm.semantic import SemanticReadModel

_SOURCE = "calendar"


class CalendarProvider:
    id = "calendar"

    def __init__(self, config: CalendarConfig) -> None:
        self._config = config

    def events(
        self, window: ContextWindow, scope: dict[str, str], read_model: SemanticReadModel
    ) -> list[ContextEvent]:
        del scope, read_model
        cfg = self._config
        out: list[ContextEvent] = []
        lead = timedelta(days=cfg.pre_festival_days)
        for festival in cfg.festivals:
            for raw in festival.dates:
                day = date.fromisoformat(raw)
                if window.overlaps(day - lead, day):
                    out.append(ContextEvent(kind="festival", start=day - lead, end=day,
                                            source=_SOURCE, title=festival.id))
        days = [window.start + timedelta(days=i) for i in range((window.end - window.start).days + 1)]
        out += _runs("payday", [d for d in days if is_payday(d, cfg)])
        out += _runs("month_end", [d for d in days if is_month_end(d, cfg)])
        return sorted(out, key=lambda e: (e.start, e.kind))


def _runs(kind: str, days: list[date]) -> list[ContextEvent]:
    """Consecutive days collapse into one event."""
    out: list[ContextEvent] = []
    for day in days:
        if out and out[-1].end + timedelta(days=1) == day:
            out[-1] = out[-1].model_copy(update={"end": day})
        else:
            out.append(ContextEvent(kind=kind, start=day, end=day, source=_SOURCE, title=kind))
    return out
