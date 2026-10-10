"""Context providers with an invented catalogue and injected configs."""

from __future__ import annotations

from datetime import date

from seleric_swarm.context import (
    CalendarProvider,
    ContextWindow,
    FreshnessHealth,
    IncidentProvider,
    disabled_ids,
    gather_events,
    gather_health,
)
from seleric_swarm.forecasting.calendar import CalendarConfig, FestivalEntry
from seleric_swarm.forecasting.quality import Incident, IncidentsFile
from seleric_swarm.semantic import SemanticReadModel

MODEL = SemanticReadModel.from_payload({
    "catalogue_version": "zz-v1",
    "metrics": [{"id": "zz_take", "view": "zz_view"}, {"id": "zz_count", "view": "zz_other"}],
    "views": [{"id": "zz_view", "freshness": "stale"}, {"id": "zz_other", "freshness": "fresh"}],
})
OCT = ContextWindow(start=date(2026, 10, 1), end=date(2026, 10, 10))
CAL = CalendarConfig(festivals=[FestivalEntry(id="zz_fest", dates=["2026-10-05", "2026-12-01"])], pre_festival_days=2)
INC = IncidentsFile(incidents=[
    Incident(id="gap", start="2026-10-03", end="2026-10-04", metrics=["zz_take"], reason="replay"),
    Incident(id="gone", start="2026-10-03", end="2026-10-04", metrics=["zz_removed"]),
    Incident(id="all", start="2026-09-01", end="2026-10-01"),
    Incident(id="old", start="2026-01-01", end="2026-01-02"),
])


def test_calendar_festival_with_lead_and_payday_runs() -> None:
    events = CalendarProvider(CAL).events(OCT, {}, MODEL)
    fest = [e for e in events if e.kind == "festival"]
    assert [(e.title, e.start, e.end) for e in fest] == [("zz_fest", date(2026, 10, 3), date(2026, 10, 5))]
    paydays = [e for e in events if e.kind == "payday"]
    assert paydays and paydays[0].start == date(2026, 10, 1) and paydays[0].end == date(2026, 10, 2)


def test_incidents_skip_unknown_metrics_and_other_windows() -> None:
    events = IncidentProvider(INC).events(OCT, {}, MODEL)
    assert {e.title for e in events} == {"gap", "all"}


def test_incident_health_per_metric() -> None:
    health = IncidentProvider(INC).health(OCT, ("zz_take", "zz_count"), MODEL)
    assert {(h.metric_id, h.detail.split()[0]) for h in health} == {("zz_take", "gap"), ("zz_take", "all"), ("zz_count", "all")}
    assert all(h.status == "incident" for h in health)


def test_freshness_reports_only_stale_views() -> None:
    health = FreshnessHealth().health(OCT, ("zz_take", "zz_count", "zz_missing"), MODEL)
    assert [(h.metric_id, h.status) for h in health] == [("zz_take", "late")]


def test_disabled_provider_returns_nothing() -> None:
    off = disabled_ids({"providers": {"calendar": {"enabled": False}, "incidents": {"enabled": True}}})
    assert off == {"calendar"}
    events = gather_events([CalendarProvider(CAL), IncidentProvider(INC)], OCT, {}, MODEL, disabled=off)
    assert {e.source for e in events} == {"incidents"}


def test_failing_provider_is_skipped() -> None:
    class Boom:
        id = "boom"

        def events(self, *a: object):  # type: ignore[no-untyped-def]
            raise RuntimeError

        def health(self, *a: object):  # type: ignore[no-untyped-def]
            raise RuntimeError

    assert gather_events([Boom(), IncidentProvider(INC)], OCT, {}, MODEL)
    assert gather_health([Boom(), FreshnessHealth()], OCT, ("zz_take",), MODEL)
