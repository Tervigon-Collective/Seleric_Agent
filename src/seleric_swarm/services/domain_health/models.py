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
    rolling_std_7d: float | None = None
    direction_bad: Literal["up", "down"] | None = None
    freshness: Freshness = "UNKNOWN"
    # Robust z-score block (detectors.py) when the profile entry sets
    # `anomaly: true`; None otherwise. Powers the ready-store fast path.
    anomaly: dict[str, Any] | None = None
    quality_flags: list[QualityFlag] = Field(default_factory=list)
    # Added by the monitoring panel. All optional so older snapshots on disk still load.
    name: str | None = None            # catalogue description
    label: str | None = None           # the user's own label, if they set one
    unit: str | None = None
    display: str | None = None         # percent | multiple | currency | count | seconds
    shape: str | None = None           # additive | ratio | signed (judge.infer_shape)
    series: list[dict[str, Any]] = Field(default_factory=list)  # last 28 daily {ts, value}
    judgement: dict[str, Any] | None = None  # judge.Judgement.as_dict()
    observed_on: str | None = None     # the day ``value`` belongs to; differs from the snapshot day when the source had no row
    status: str = "no_data"            # alert | watch | good | ok | no_data
    polarity_known: bool = False       # False: the catalogue gives no direction_bad, so no alerting
    rule: dict[str, Any] | None = None


class DomainStateSnapshot(BaseModel):
    domain: str
    title: str | None = None
    brand_id: str
    as_of: str
    computed_at: str
    window: dict[str, Any] = Field(default_factory=dict)
    status: DomainStatus = "UNAVAILABLE"
    metrics: list[ResolvedMetric] = Field(default_factory=list)
    headline_signals: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
