from __future__ import annotations

from pathlib import Path

from seleric_swarm.paths import repo_root
from seleric_swarm.services.domain_health.models import DomainStateSnapshot


class SnapshotStore:
    """One JSON file per (domain, as_of) on disk.

    [2026-09-14 decision] JSON now, Postgres JSONB later -- same
    get_latest/save interface either way, so the migration is a rewrite of
    this file, not a change to resolver.py or any caller (see
    docs/features/business-state-service/04_DOMAIN_HEALTH_SNAPSHOTS.md).
    """

    def __init__(self, base_dir: str | Path = "var/domain_health_snapshots") -> None:
        path = Path(base_dir)
        self._base = path if path.is_absolute() else repo_root() / path

    def save(self, snapshot: DomainStateSnapshot) -> Path:
        domain_dir = self._base / snapshot.domain
        domain_dir.mkdir(parents=True, exist_ok=True)
        file_path = domain_dir / f"{snapshot.as_of}.json"
        file_path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
        return file_path

    def get_latest(self, domain: str) -> DomainStateSnapshot | None:
        domain_dir = self._base / domain
        if not domain_dir.is_dir():
            return None
        files = sorted(domain_dir.glob("*.json"))
        if not files:
            return None
        return DomainStateSnapshot.model_validate_json(files[-1].read_text(encoding="utf-8"))
