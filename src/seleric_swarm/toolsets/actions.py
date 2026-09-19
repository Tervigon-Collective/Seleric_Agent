"""ActionToolset: guarded writes through the live Seleric MCP action broker.

The remote broker owns payload validation, business rules, Meta/Google Ads
execution, its write kill switch, audit records, and 24-hour payload
idempotency. This adapter owns the frozen v3 tool contract and deliberately
keeps the broker's short-lived confirmation token out of model-visible output.

``commit_action`` is only called after the user explicitly confirms the
sanitized preview. There is no separate ``confirm`` tool in the frozen
contract: confirmation is the user turn between ``preview`` and ``commit``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai import RunContext

from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.conversations.contracts import ArtifactProvenance

_AGENT_ID = "v3_agent"


@dataclass
class _ProposalState:
    preview: dict[str, Any]
    confirmation_token: str | None


# Tokens expire after roughly five minutes on the server. Process-local state
# is therefore intentional: after a restart the safe behavior is to re-propose
# and obtain a fresh preview/token, never to persist a bearer token.
_PROPOSALS: dict[tuple[int, str, str], _ProposalState] = {}
_COMMIT_KEYS: dict[tuple[int, str, str], str] = {}


def _proposal_key(ctx: RunContext[SelericDeps], approval_id: str) -> tuple[int, str, str]:
    return (id(ctx.deps.mcp_client), ctx.deps.principal.workspace_id, approval_id)


def _commit_key(ctx: RunContext[SelericDeps], idempotency_key: str) -> tuple[int, str, str]:
    return (id(ctx.deps.mcp_client), ctx.deps.principal.workspace_id, idempotency_key)


def _sanitized(payload: dict[str, Any]) -> dict[str, Any]:
    safe = dict(payload)
    safe.pop("confirmation_token", None)
    return safe


def _mcp_error(exc: Exception) -> ToolResult:
    return ToolResult(
        success=False,
        summary=f"{type(exc).__name__}: {exc}",
        error_code="MCP_UNAVAILABLE",
        retryable=True,
    )


def _remote_error(payload: dict[str, Any], *, code: str) -> ToolResult:
    return ToolResult(
        success=False,
        summary=str(payload.get("error") or "action broker rejected the request"),
        error_code=code,
        retryable=False,
        provenance=ArtifactProvenance(source_metadata=_sanitized(payload)),
    )


async def _status(ctx: RunContext[SelericDeps], approval_id: str) -> dict[str, Any]:
    result = await ctx.deps.mcp_client.call(
        agent_id=_AGENT_ID,
        capability="seleric.actions_status",
        arguments={"action_request_id": approval_id},
    )
    return dict(result or {})


async def propose_action(
    ctx: RunContext[SelericDeps], action_type: str, action_params: dict[str, Any]
) -> ToolResult:
    """Validate and preview an action; never executes the write."""
    try:
        raw = await ctx.deps.mcp_client.call(
            agent_id=_AGENT_ID,
            capability="seleric.actions_propose",
            arguments={"action_id": action_type, "payload": action_params},
        )
    except Exception as exc:
        return _mcp_error(exc)
    result = dict(raw or {})
    if result.get("error"):
        return _remote_error(result, code="ACTION_REJECTED")
    approval_id = str(result.get("action_request_id") or "")
    if not approval_id:
        return ToolResult(
            success=False,
            summary="action broker returned no action_request_id",
            error_code="MALFORMED_ACTION_RESPONSE",
            retryable=False,
        )

    token = result.get("confirmation_token")
    _PROPOSALS[_proposal_key(ctx, approval_id)] = _ProposalState(
        preview=_sanitized(result),
        confirmation_token=str(token) if token else None,
    )
    eligible = bool(result.get("eligible"))
    warnings = []
    if not eligible:
        warnings.append("proposal is not eligible and cannot be committed")
    if not result.get("write_enabled", True):
        warnings.append("remote write kill switch is disabled")
    return ToolResult(
        success=True,
        summary=(
            f"action proposal {approval_id} is "
            f"{'eligible; explicit user confirmation is required' if eligible else 'not eligible'}"
        ),
        warnings=warnings,
        provenance=ArtifactProvenance(source_metadata=_sanitized(result)),
    )


async def validate(ctx: RunContext[SelericDeps], approval_id: str) -> ToolResult:
    """Check that the broker still knows the proposal and it remains commit-eligible."""
    try:
        status = await _status(ctx, approval_id)
    except Exception as exc:
        return _mcp_error(exc)
    if status.get("error"):
        return _remote_error(status, code="ACTION_NOT_FOUND")

    proposal = _PROPOSALS.get(_proposal_key(ctx, approval_id))
    if proposal is not None and not proposal.preview.get("eligible"):
        return ToolResult(
            success=False,
            summary=f"action proposal {approval_id} is not eligible",
            error_code="ACTION_NOT_ELIGIBLE",
            retryable=False,
            provenance=ArtifactProvenance(source_metadata=proposal.preview),
        )
    terminal_failure = str(status.get("status") or "").upper() in {
        "FAILED",
        "REJECTED",
        "EXPIRED",
    }
    if terminal_failure:
        return ToolResult(
            success=False,
            summary=f"action proposal {approval_id} is {status.get('status')}",
            error_code="ACTION_NOT_ELIGIBLE",
            retryable=False,
            provenance=ArtifactProvenance(source_metadata=_sanitized(status)),
        )
    warnings = (
        []
        if proposal is not None
        else ["local confirmation token unavailable; re-propose before commit"]
    )
    return ToolResult(
        success=True,
        summary=f"action proposal {approval_id} is valid ({status.get('status', 'unknown')})",
        warnings=warnings,
        provenance=ArtifactProvenance(source_metadata=_sanitized(status)),
    )


async def preview(ctx: RunContext[SelericDeps], approval_id: str) -> ToolResult:
    """Return the sanitized proposal preview for display before confirmation."""
    try:
        status = await _status(ctx, approval_id)
    except Exception as exc:
        return _mcp_error(exc)
    if status.get("error"):
        return _remote_error(status, code="ACTION_NOT_FOUND")
    proposal = _PROPOSALS.get(_proposal_key(ctx, approval_id))
    metadata = {"status": _sanitized(status)}
    warnings: list[str] = []
    if proposal is not None:
        metadata["preview"] = proposal.preview
    else:
        warnings.append("full preview unavailable after restart; re-propose before commit")
    return ToolResult(
        success=True,
        summary=f"preview for action proposal {approval_id}; show it and request explicit confirmation",
        warnings=warnings,
        provenance=ArtifactProvenance(source_metadata=metadata),
    )


async def commit_action(
    ctx: RunContext[SelericDeps], approval_id: str, idempotency_key: str
) -> ToolResult:
    """Commit an explicitly confirmed proposal exactly once for this key."""
    if not idempotency_key.strip():
        return ToolResult(
            success=False,
            summary="idempotency_key must not be empty",
            error_code="INVALID_IDEMPOTENCY_KEY",
            retryable=False,
        )
    key = _commit_key(ctx, idempotency_key)
    prior = _COMMIT_KEYS.get(key)
    if prior is not None and prior != approval_id:
        return ToolResult(
            success=False,
            summary=f"idempotency key is already bound to action proposal {prior}",
            error_code="IDEMPOTENCY_CONFLICT",
            retryable=False,
        )
    if prior == approval_id:
        try:
            status = await _status(ctx, approval_id)
        except Exception as exc:
            return _mcp_error(exc)
        state = str(status.get("status") or "").upper()
        return ToolResult(
            success=state in {"EXECUTED", "DUPLICATE"},
            summary=f"idempotent commit status for {approval_id}: {state or 'unknown'}",
            error_code=None if state in {"EXECUTED", "DUPLICATE"} else "ACTION_COMMIT_IN_PROGRESS",
            retryable=state not in {"EXECUTED", "DUPLICATE", "FAILED", "REJECTED", "EXPIRED"},
            provenance=ArtifactProvenance(source_metadata=_sanitized(status)),
        )

    proposal = _PROPOSALS.get(_proposal_key(ctx, approval_id))
    if proposal is None or not proposal.confirmation_token:
        return ToolResult(
            success=False,
            summary=f"no live confirmation token for {approval_id}; re-propose and confirm again",
            error_code="ACTION_NOT_APPROVED",
            retryable=False,
        )

    _COMMIT_KEYS[key] = approval_id
    try:
        raw = await ctx.deps.mcp_client.call(
            agent_id=_AGENT_ID,
            capability="seleric.actions_commit",
            arguments={"confirmation_token": proposal.confirmation_token},
        )
    except Exception as exc:
        _COMMIT_KEYS.pop(key, None)
        return _mcp_error(exc)
    result = dict(raw or {})
    proposal.confirmation_token = None
    if result.get("error"):
        return _remote_error(result, code="ACTION_COMMIT_FAILED")
    state = str(result.get("status") or "").upper()
    succeeded = state in {"EXECUTED", "DUPLICATE"}
    return ToolResult(
        success=succeeded,
        summary=f"action proposal {approval_id} commit result: {state or 'unknown'}",
        error_code=None if succeeded else "ACTION_COMMIT_FAILED",
        retryable=False,
        provenance=ArtifactProvenance(source_metadata=_sanitized(result)),
    )
