"""SnapshotStore atomic writes + corrupt-file skip."""

from __future__ import annotations

import pytest

from seleric_swarm.services.domain_health.models import DomainStateSnapshot, ResolvedMetric
from seleric_swarm.services.domain_health.snapshot_store import SnapshotStore, _safe_path_segment


def _snap(domain: str = "commerce", as_of: str = "2026-09-15") -> DomainStateSnapshot:
    return DomainStateSnapshot(
        domain=domain,
        brand_id="20",
        as_of=as_of,
        computed_at="2026-09-15T00:00:00+00:00",
        status="OK",
        metrics=[ResolvedMetric(metric_id="metric.net_sales", value=1.0)],
        headline_signals=[],
    )


def test_save_is_atomic_and_readable(tmp_path):
    store = SnapshotStore(base_dir=tmp_path)
    path = store.save(_snap())
    assert path.exists()
    assert not list(tmp_path.rglob("*.tmp"))
    latest = store.get_latest("commerce")
    assert latest is not None
    assert latest.as_of == "2026-09-15"


def test_get_latest_skips_corrupt_json(tmp_path):
    store = SnapshotStore(base_dir=tmp_path)
    store.save(_snap(as_of="2026-09-14"))
    bad = tmp_path / "commerce" / "2026-09-15.json"
    bad.write_text("{not-json", encoding="utf-8")
    latest = store.get_latest("commerce")
    assert latest is not None
    assert latest.as_of == "2026-09-14"


def test_unsafe_path_segment_rejected():
    with pytest.raises(ValueError):
        _safe_path_segment("../etc", label="domain")
    with pytest.raises(ValueError):
        store = SnapshotStore(base_dir=".")
        store.get_latest("commerce/../finance")
