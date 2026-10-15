"""Dated facts that are not metric series: lever changes, price or stock moves, incidents."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import ClassVar, Protocol

from seleric_swarm.diagnosis.domain.types import Scope, Window


@dataclass(frozen=True)
class DatedEvidence:
    kind: str
    day: date
    subject: str
    detail: str
    source: str


class EvidencePort(Protocol):
    kind: ClassVar[str]  # lever | price | stock | incident

    async def events(self, scope: Scope, window: Window) -> Sequence[DatedEvidence]: ...
