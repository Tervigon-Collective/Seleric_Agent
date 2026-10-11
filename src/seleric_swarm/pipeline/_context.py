"""The mission's context providers and health sources, built from config (M4.1 wiring)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import yaml

from seleric_swarm.context import (
    AsyncContextProvider,
    CalendarProvider,
    ChangeLogProvider,
    ContextProvider,
    EventReader,
    FreshnessHealth,
    HealthSource,
    IncidentProvider,
    SnapshotHealth,
    disabled_ids,
)
from seleric_swarm.paths import repo_root

_log = logging.getLogger("seleric.pipeline.context")


def _config() -> dict[str, Any]:
    path = repo_root() / "config" / "context_providers.yaml"
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def latest_snapshots(base: Path | None = None) -> list[dict[str, Any]]:
    """The newest domain-health snapshot per domain, as plain dicts.

    Reads the snapshot directory directly (same env as ``services.domain_health.SnapshotStore``)
    so the pipeline does not import the services layer.
    """
    raw = base if base is not None else Path(os.getenv("SELERIC_DOMAIN_HEALTH_DIR", "var/domain_health_snapshots"))
    root = raw if raw.is_absolute() else repo_root() / raw
    out: list[dict[str, Any]] = []
    try:
        for domain_dir in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
            files = sorted(f for f in domain_dir.glob("*.json") if not f.name.startswith("."))
            if files:
                out.append(json.loads(files[-1].read_text(encoding="utf-8")))
    except (OSError, ValueError):
        _log.warning("domain_health_snapshots_unavailable", exc_info=True)
    return out


def build_context(
    reader: EventReader | None,
) -> tuple[list[ContextProvider | AsyncContextProvider], list[HealthSource]]:
    """Enabled providers and health sources; one that cannot load is skipped."""
    providers: list[ContextProvider | AsyncContextProvider] = []
    sources: list[HealthSource] = [FreshnessHealth()]
    try:
        from seleric_swarm.forecasting.calendar import load_calendar_config

        providers.append(CalendarProvider(load_calendar_config()))
    except Exception:
        _log.warning("calendar_provider_unavailable", exc_info=True)
    try:
        from seleric_swarm.forecasting.quality import load_incidents

        incidents = IncidentProvider(load_incidents())
        providers.append(incidents)
        sources.append(incidents)
    except Exception:
        _log.warning("incident_provider_unavailable", exc_info=True)
    if reader is not None:
        providers.append(ChangeLogProvider(reader))
    sources.append(SnapshotHealth(latest_snapshots()))
    off = disabled_ids(_config())
    return [p for p in providers if p.id not in off], [s for s in sources if s.id not in off]
