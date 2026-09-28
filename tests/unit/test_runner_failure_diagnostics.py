"""``_failure_diagnostics`` must make a failed mission locatable from its
request_id (P1). The live incident (mission MS3-63e209f4b0) surfaced only
``V3_AGENT_FAILED`` with no tool name, cause, or retry count — this reconstructs
that exact exception chain and asserts the structured fields come back.
"""

from __future__ import annotations

from pydantic_ai.exceptions import ModelRetry, UnexpectedModelBehavior

from seleric_swarm.agent.runner import _failure_diagnostics


def _tool_retry_exhausted() -> UnexpectedModelBehavior:
    """The MS3-63e209f4b0 signature: an anti-loop ModelRetry chained under the
    max-retries UnexpectedModelBehavior pydantic-ai raises on exhaustion."""
    try:
        try:
            raise ModelRetry("You have already fetched this exact query — call final_result now.")
        except ModelRetry as guard:
            raise UnexpectedModelBehavior(
                "Tool 'query_metrics' exceeded max retries count of 2"
            ) from guard
    except UnexpectedModelBehavior as exc:
        return exc


def test_tool_retry_exhaustion_yields_tool_name_count_and_cause() -> None:
    diag = _failure_diagnostics(_tool_retry_exhausted())
    assert diag["stage"] == "agent_run"
    assert diag["failing_tool"] == "query_metrics"
    assert diag["retry_count"] == 2
    assert diag["cause_code"] == "TOOL_MAX_RETRIES_EXCEEDED"
    assert diag["exception_type"] == "UnexpectedModelBehavior"
    # The original guard error is preserved for the log (not the user trace).
    assert diag["cause_type"] == "ModelRetry"
    assert "already fetched" in diag["cause_detail"]


def test_generic_failure_still_reports_type_without_tool_fields() -> None:
    diag = _failure_diagnostics(RuntimeError("something else broke"))
    assert diag["stage"] == "agent_run"
    assert diag["exception_type"] == "RuntimeError"
    assert "failing_tool" not in diag
    assert "cause_code" not in diag
