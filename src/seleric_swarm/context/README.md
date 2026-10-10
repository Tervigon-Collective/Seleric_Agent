# `context`

## Purpose
Events (festivals, paydays, month ends, data incidents) and data health that diagnosis (M4.3) cites. Pure: configs are injected, catalogue facts come from `SemanticReadModel`.

## Interface
- `ContextProvider.events(window, scope, read_model) -> list[ContextEvent]`: `CalendarProvider(CalendarConfig)`, `IncidentProvider(IncidentsFile)`.
- `HealthSource.health(window, metric_ids, read_model) -> list[DataHealth]`: `IncidentProvider`, `FreshnessHealth` (view freshness).
- `gather_events` / `gather_health` take `disabled=disabled_ids(<config/context_providers.yaml>)`.

## Failure policy
A failing provider is logged and skipped. Incident metric ids the catalogue no longer has are dropped (semantic pickup).

## Gaps
`ChangeLogProvider` (catalogue change-event metrics) and domain-health snapshots are not built yet.

## Tests
`uv run pytest tests/unit/context`
