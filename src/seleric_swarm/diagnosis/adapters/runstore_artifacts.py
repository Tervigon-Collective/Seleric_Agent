"""Run records in the existing artifact store (D-40), scoped to the mission, so they are deleted with their thread
and kept for the retention window of D-41. The id is derived from the investigation id, so load needs no index."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.diagnosis.domain.record import RunRecord
from seleric_swarm.diagnosis.ports.runstore import RunRecordNotFound
from seleric_swarm.state.artifacts import ArtifactStore

_log = logging.getLogger("seleric.diagnosis.trace")
ARTIFACT_TYPE = "diagnosis_run_record"
RETENTION_DAYS = 90  # D-41, D-49
CALCULATION_VERSION = "diagnosis-run-record/1"


def artifact_id(investigation_id: str) -> str:
    return f"diagrun_{investigation_id}"


class ArtifactRunStore:
    def __init__(
        self,
        store: ArtifactStore,
        *,
        workspace_id: str,
        mission_id: str | None = None,
        thread_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self._store = store
        self._workspace_id = workspace_id
        self._mission_id = mission_id
        self._thread_id = thread_id
        self._run_id = run_id

    def save(self, record: RunRecord) -> None:
        # Records expire (D-49): every save removes the ones past the window. A failure is loud but does not stop this
        # record being kept.
        try:
            purge_expired(self._store)
        except Exception:
            _log.error("run_record_expiry_failed", exc_info=True)
        # The evidence of a run record is the data it was computed from: one id per recorded series answer.
        evidence = sorted({f"series_snapshot:{s.fingerprint}" for s in record.snapshots}) or [f"run:{record.investigation_id}"]
        self._store.put(
            Artifact(
                id=artifact_id(record.investigation_id),
                workspace_id=self._workspace_id,
                artifact_type=ARTIFACT_TYPE,
                payload=record.to_dict(),
                classification="derived",
                evidence_ids=evidence,
                provenance=ArtifactProvenance(
                    evidence_ids=evidence,
                    calculation_version=CALCULATION_VERSION,
                    source_metadata={"investigation_id": record.investigation_id},
                ),
                mission_id=self._mission_id,
                thread_id=self._thread_id,
                run_id=self._run_id,
            )
        )

    def load(self, investigation_id: str) -> RunRecord:
        artifact = self._store.get(artifact_id(investigation_id))
        if artifact is None or artifact.artifact_type != ARTIFACT_TYPE:
            raise RunRecordNotFound(investigation_id)
        return RunRecord.from_dict(artifact.payload)


def purge_expired(store: ArtifactStore, *, now: datetime | None = None) -> int:
    """Delete run records older than the retention window; returns how many."""
    return store.delete_where(ARTIFACT_TYPE, created_before=(now or datetime.now(UTC)) - timedelta(days=RETENTION_DAYS))


def purge_thread(store: ArtifactStore, thread_id: str) -> int:
    """Delete a thread's run records (called when the thread is deleted); returns how many."""
    return store.delete_where(ARTIFACT_TYPE, thread_id=thread_id)
