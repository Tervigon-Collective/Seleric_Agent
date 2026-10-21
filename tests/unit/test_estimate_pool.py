"""The per-driver estimates of a diagnosis run in worker processes; any pool failure falls back to in-process."""

from __future__ import annotations

from typing import Any

import pytest

from seleric_swarm.causal import estimate_pool
from seleric_swarm.causal.dowhy_service import TimeSeriesEffect


def _effect(**kwargs: Any) -> TimeSeriesEffect:
    return TimeSeriesEffect(treatment=kwargs["treatment_name"], outcome="y", status="insufficient", reason="stub")


def test_jobs_run_in_order_without_a_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIAGNOSIS_WORKERS", "0")
    monkeypatch.setattr(estimate_pool, "estimate_time_series_effect", _effect)
    out = estimate_pool.estimate_many([{"treatment_name": "a"}, {"treatment_name": "b"}, {"treatment_name": "c"}])
    assert [e.treatment for e in out] == ["a", "b", "c"]


def test_a_broken_pool_falls_back_to_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken:
        def map(self, *_a: Any, **_k: Any) -> Any:
            raise RuntimeError("worker died")

        def shutdown(self, **_k: Any) -> None:
            pass

    monkeypatch.setattr(estimate_pool, "_get_pool", lambda: Broken())
    monkeypatch.setattr(estimate_pool, "estimate_time_series_effect", _effect)
    out = estimate_pool.estimate_many([{"treatment_name": "a"}, {"treatment_name": "b"}])
    assert [e.treatment for e in out] == ["a", "b"]


def test_workers_without_cpu_affinity_stay_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DIAGNOSIS_WORKERS", raising=False)
    monkeypatch.delattr(estimate_pool.os, "sched_getaffinity", raising=False)
    assert estimate_pool.workers() == 0


def test_a_single_job_never_starts_a_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_pool() -> Any:
        raise AssertionError("pool requested for one job")

    monkeypatch.setattr(estimate_pool, "_get_pool", no_pool)
    monkeypatch.setattr(estimate_pool, "estimate_time_series_effect", _effect)
    assert estimate_pool.estimate_many([{"treatment_name": "a"}])[0].treatment == "a"
