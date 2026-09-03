from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from seleric_swarm.domain.models import EvidenceArtifact


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def make_evidence(
    *,
    source: str,
    metric_or_fact: str,
    value: Any,
    provenance: dict[str, Any],
    unit: str | None = None,
    dimensions: dict[str, Any] | None = None,
    time_range: dict[str, Any] | None = None,
    freshness: str | None = None,
    quality_flags: list[str] | None = None,
) -> dict[str, Any]:
    retrieved_at = utc_now()
    artifact = EvidenceArtifact(
        evidence_id=f"EV-{uuid4().hex[:12]}",
        source=source,
        metric_or_fact=metric_or_fact,
        value=value,
        unit=unit,
        dimensions=dimensions or {},
        provenance=provenance,
        quality_flags=quality_flags or [],
        retrieved_at=retrieved_at,
        time_range=time_range or {},
        freshness=freshness or retrieved_at,
    )
    return artifact.model_dump()
