from __future__ import annotations
from typing import Any
from uuid import uuid4


def make_evidence(*, source: str, metric_or_fact: str, value: Any, provenance: dict[str, Any]) -> dict:
    return {
        "evidence_id": f"EV-{uuid4().hex[:12]}",
        "source": source,
        "metric_or_fact": metric_or_fact,
        "value": value,
        "provenance": provenance,
        "quality_flags": [],
    }
