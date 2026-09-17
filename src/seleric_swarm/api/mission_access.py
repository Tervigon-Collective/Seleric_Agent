"""Ownership policy for legacy mission-backed HTTP surfaces."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from seleric_swarm.conversations.contracts import Principal


def request_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal) or not principal.authenticated:
        raise HTTPException(status_code=401, detail="authentication required")
    return principal


def can_access_mission(
    principal: Principal,
    raw: dict[str, Any],
    *,
    default_workspace_id: str = "default",
    default_user_id: str = "default",
) -> bool:
    workspace_id = raw.get("workspace_id")
    owner_user_id = raw.get("owner_user_id")
    if workspace_id and owner_user_id:
        return principal.owns(
            workspace_id=str(workspace_id), user_id=str(owner_user_id)
        )
    # Records created before ownership columns existed remain visible only to the
    # configured compatibility principal (or an internal administrator).
    return principal.is_internal or (
        principal.workspace_id == default_workspace_id
        and principal.user_id == default_user_id
    )


def require_mission_access(
    request: Request, raw: dict[str, Any], runtime: Any
) -> Principal:
    principal = request_principal(request)
    settings = getattr(runtime, "settings", None)
    if not can_access_mission(
        principal,
        raw,
        default_workspace_id=str(
            getattr(settings, "default_workspace_id", "default")
        ),
        default_user_id=str(getattr(settings, "default_user_id", "default")),
    ):
        raise HTTPException(status_code=404, detail="mission not found")
    return principal
