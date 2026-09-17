from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from seleric_swarm.paths import repo_root
from seleric_swarm.services.domain_health.models import DomainStateSnapshot

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


def _safe_path_segment(value: str, *, label: str) -> str:
    text = (value or "").strip()
    if not text or not _SAFE_SEGMENT.fullmatch(text) or ".." in text:
        raise ValueError(f"unsafe {label} for snapshot path: {value!r}")
    return text


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
        domain = _safe_path_segment(snapshot.domain, label="domain")
        as_of = _safe_path_segment(snapshot.as_of, label="as_of")
        domain_dir = self._base / domain
        domain_dir.mkdir(parents=True, exist_ok=True)
        file_path = domain_dir / f"{as_of}.json"
        tmp_path = domain_dir / f".{as_of}.json.tmp"
        tmp_path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp_path, file_path)
        return file_path

    def get_latest(self, domain: str) -> DomainStateSnapshot | None:
        domain = _safe_path_segment(domain, label="domain")
        domain_dir = self._base / domain
        if not domain_dir.is_dir():
            return None
        files = sorted(p for p in domain_dir.glob("*.json") if not p.name.startswith("."))
        for file_path in reversed(files):
            try:
                return DomainStateSnapshot.model_validate_json(file_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: S112 - skip corrupt snapshots and try the next newest
                continue
        return None

    async def asave(self, snapshot: DomainStateSnapshot) -> Path:
        return await asyncio.to_thread(self.save, snapshot)

    async def aget_latest(self, domain: str) -> DomainStateSnapshot | None:
        return await asyncio.to_thread(self.get_latest, domain)
