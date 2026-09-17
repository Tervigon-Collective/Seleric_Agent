from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from seleric_swarm.domain.models import Freshness, QualityFlag

# Domain Health Snapshot contracts (docs/features/business-state-service/04_DOMAIN_HEALTH_SNAPSHOTS.md).
# Sprint 3: commerce only, JSON-file persistence (see snapshot_store.py).
DomainStatus = Literal["OK", "DEGRADED", "UNAVAILABLE"]


class ResolvedMetric(BaseModel):
    metric_id: str
    value: float | None = None
    period_delta_pct: float | None = None
    rolling_mean_7d: float | None = None
    direction_bad: Literal["up", "down"] | None = None
    freshness: Freshness = "UNKNOWN"
    quality_flags: list[QualityFlag] = Field(default_factory=list)


class DomainStateSnapshot(BaseModel):
    domain: str
    brand_id: str
    as_of: str
    computed_at: str
    window: dict[str, Any] = Field(default_factory=dict)
    status: DomainStatus = "UNAVAILABLE"
    metrics: list[ResolvedMetric] = Field(default_factory=list)
    headline_signals: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
