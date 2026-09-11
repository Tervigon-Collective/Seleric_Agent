from __future__ import annotations

import time

from seleric_swarm.llm.circuit_breaker import CircuitBreaker


def test_starts_closed_and_allows():
    cb = CircuitBreaker()
    assert cb.state == "closed"
    assert cb.allow()


def test_opens_after_threshold_consecutive_failures():
    cb = CircuitBreaker(failure_threshold=3, cooldown_s=60)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "closed"
    cb.record_failure()
    assert cb.state == "open"
    assert not cb.allow()


def test_success_resets_failure_count():
    cb = CircuitBreaker(failure_threshold=2, cooldown_s=60)
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    assert cb.state == "closed"  # would have opened at 2 consecutive without the reset


def test_half_opens_after_cooldown_and_allows_probe():
    cb = CircuitBreaker(failure_threshold=1, cooldown_s=0.01)
    cb.record_failure()
    assert cb.state == "open"
    time.sleep(0.02)
    assert cb.state == "half_open"
    assert cb.allow()


def test_failed_probe_reopens_and_restarts_cooldown():
    cb = CircuitBreaker(failure_threshold=1, cooldown_s=0.01)
    cb.record_failure()
    time.sleep(0.02)
    assert cb.allow()  # half-open probe window
    cb.record_failure()  # probe failed
    assert cb.state == "open"
    assert not cb.allow()
