"""Retry classification — retryable vs blocking failures."""

from __future__ import annotations

from typing import Literal

FailureClass = Literal["success", "retryable_failure", "blocking_failure", "nonblocking_failure"]

_RETRYABLE = {
    "timeout",
    "unavailable",
    "a2a_error",
    "network",
    "service_unavailable",
    "invoke_error",
    "rate_limited",
}
_BLOCKING = {
    "missing_causal_graph",
    "invalid_metric",
    "schema_error",
    "policy_violation",
    "unsupported_capability",
    "agent_unavailable",
}


def classify_failure(error_code: str | None, *, status: str | None = None) -> FailureClass:
    if status == "success" or error_code in {None, "", "ok"}:
        return "success"
    code = (error_code or "").lower()
    if code in _BLOCKING or any(k in code for k in _BLOCKING):
        return "blocking_failure"
    if code in _RETRYABLE or any(k in code for k in ("timeout", "unavailable", "network", "a2a")):
        return "retryable_failure"
    return "nonblocking_failure"


def should_retry(failure: FailureClass, *, attempt: int, max_retries: int = 2) -> bool:
    return failure == "retryable_failure" and attempt < max_retries
