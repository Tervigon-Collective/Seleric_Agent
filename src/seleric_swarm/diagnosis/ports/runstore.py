"""Where run records are kept."""

from __future__ import annotations

from typing import Protocol

from seleric_swarm.diagnosis.domain.errors import DiagnosisError
from seleric_swarm.diagnosis.domain.record import RunRecord


class RunRecordNotFound(DiagnosisError):
    code = "run_record_not_found"


class RunStore(Protocol):
    def save(self, record: RunRecord) -> None:
        """Keep the record. A record is written once; saving an id that exists is an error."""
        ...

    def load(self, investigation_id: str) -> RunRecord:
        """Raises ``RunRecordNotFound``."""
        ...
