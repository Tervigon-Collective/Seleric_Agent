"""The trace event envelope (plan §5.2). Every step of an investigation is one ordered, serialisable event."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from seleric_swarm.diagnosis.domain.canonical import canonical_json

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Event:
    """``seq`` is 1-based and gap-free within an investigation; ``parent_id`` is the ``seq`` of the event this one
    belongs under (an operator run under its hypothesis, a query under its operator), or None at the top."""

    investigation_id: str
    seq: int
    ts: datetime
    depth: int
    branch: int
    parent_id: int | None
    kind: str
    schema_version: int
    payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "investigation_id": self.investigation_id, "seq": self.seq, "ts": self.ts.astimezone(UTC).isoformat(),
            "depth": self.depth, "branch": self.branch, "parent_id": self.parent_id, "kind": self.kind,
            "schema_version": self.schema_version, "payload": dict(self.payload),
        }

    def to_json(self) -> str:
        """Canonical text: the same event always serialises to the same bytes (replay compares them)."""
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Event:
        return cls(
            investigation_id=str(raw["investigation_id"]), seq=int(raw["seq"]), ts=datetime.fromisoformat(raw["ts"]),
            depth=int(raw["depth"]), branch=int(raw["branch"]),
            parent_id=None if raw["parent_id"] is None else int(raw["parent_id"]), kind=str(raw["kind"]),
            schema_version=int(raw["schema_version"]), payload=dict(raw["payload"]),
        )

    @classmethod
    def from_json(cls, text: str) -> Event:
        return cls.from_dict(json.loads(text))
