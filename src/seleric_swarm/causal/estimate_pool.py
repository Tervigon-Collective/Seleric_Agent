"""Run the per-driver DoWhy estimates of one diagnosis in parallel worker processes.

Each estimate (identify, fit, refuters, placebo draws) is independent of the others and costs 1-2 s of pure CPU; the
seven of a typical diagnosis took 9.5 s one after another and 2.0 s in processes (threads: 16 s, the GIL). The pool is a
forkserver pool whose workers have already imported the numerical stack, so a job pays no import time. Any pool failure
falls back to running the jobs one by one in the calling thread: the numbers are the same either way.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Any

from seleric_swarm.causal.dowhy_service import TimeSeriesEffect, estimate_time_series_effect
from seleric_swarm.diagnosis.trace.active import report_failure

_log = logging.getLogger(__name__)

_PRELOAD = ["numpy", "pandas", "statsmodels.api", "networkx", "dowhy"]
_lock = threading.Lock()
_pool: ProcessPoolExecutor | None = None


def workers() -> int:
    """Worker processes (``DIAGNOSIS_WORKERS``; 0 turns the pool off). Default: up to 6, never more than the CPUs."""
    raw = os.environ.get("DIAGNOSIS_WORKERS", "").strip()
    if raw.isdigit():
        return int(raw)
    return max(0, min(6, len(os.sched_getaffinity(0)) - 1))


def _estimate(kwargs: dict[str, Any]) -> TimeSeriesEffect:
    return estimate_time_series_effect(**kwargs)


def _get_pool() -> ProcessPoolExecutor | None:
    global _pool
    n = workers()
    if n < 2:
        return None
    with _lock:
        if _pool is None:
            ctx = multiprocessing.get_context("forkserver")
            ctx.set_forkserver_preload(_PRELOAD)
            _pool = ProcessPoolExecutor(max_workers=n, mp_context=ctx)
        return _pool


def _reset() -> None:
    global _pool
    with _lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


def warm() -> None:
    """Start every worker now (call once at process start, from a thread): the first diagnosis then pays nothing."""
    pool = _get_pool()
    if pool is None:
        return
    try:
        list(pool.map(len, [[]] * workers()))
    except Exception:
        _log.warning("diagnosis worker pool failed to start; estimates will run in-process", exc_info=True)
        _reset()


def estimate_many(jobs: list[dict[str, Any]]) -> list[TimeSeriesEffect]:
    """``estimate_time_series_effect(**job)`` for every job, in order; parallel when a pool is available."""
    t0 = time.perf_counter()
    pool = _get_pool() if len(jobs) > 1 else None
    if pool is not None:
        try:
            out = list(pool.map(_estimate, jobs))
            _log.info("diagnosis estimates: %d jobs in %d worker processes, %.1fs", len(jobs), workers(), time.perf_counter() - t0)
            return out
        except Exception as exc:
            _log.warning("diagnosis worker pool failed; estimating in-process", exc_info=True)
            report_failure("estimate_pool", exc, {"jobs": len(jobs)})
            _reset()
    out = [estimate_time_series_effect(**job) for job in jobs]
    _log.info("diagnosis estimates: %d jobs in-process, %.1fs", len(jobs), time.perf_counter() - t0)
    return out
