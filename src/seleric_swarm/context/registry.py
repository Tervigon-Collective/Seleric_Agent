"""Enabled providers (``config/context_providers.yaml``) and the gather entry points."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from seleric_swarm.context.model import (
    ContextEvent,
    ContextProvider,
    ContextWindow,
    DataHealth,
    HealthSource,
)
from seleric_swarm.semantic import SemanticReadModel

_log = logging.getLogger("seleric.context")


def disabled_ids(config: Mapping[str, Any] | None) -> frozenset[str]:
    """``{"providers": {id: {"enabled": bool}}}``; returns the ids switched off."""
    providers = (config or {}).get("providers") or {}
    return frozenset(k for k, v in providers.items() if isinstance(v, Mapping) and v.get("enabled") is False)


def gather_events(
    providers: Sequence[ContextProvider],
    window: ContextWindow,
    scope: dict[str, str],
    read_model: SemanticReadModel,
    *,
    disabled: frozenset[str] = frozenset(),
) -> list[ContextEvent]:
    """Events from every enabled provider; a failing provider is logged and skipped."""
    out: list[ContextEvent] = []
    for p in providers:
        if p.id in disabled:
            continue
        try:
            out += p.events(window, scope, read_model)
        except Exception:
            _log.warning("context_provider_failed", exc_info=True, extra={"provider": p.id})
    return sorted(out, key=lambda e: (e.start, e.kind, e.title))


def gather_health(
    sources: Sequence[HealthSource],
    window: ContextWindow,
    metric_ids: tuple[str, ...],
    read_model: SemanticReadModel,
    *,
    disabled: frozenset[str] = frozenset(),
) -> list[DataHealth]:
    out: list[DataHealth] = []
    for s in sources:
        if s.id in disabled:
            continue
        try:
            out += s.health(window, metric_ids, read_model)
        except Exception:
            _log.warning("health_source_failed", exc_info=True, extra={"source": s.id})
    return out
