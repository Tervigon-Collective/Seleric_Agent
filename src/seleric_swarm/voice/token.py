"""``POST /v1/voice/token`` — mint a LiveKit room JWT for the calling principal.

The browser never sees ``LIVEKIT_API_SECRET``; it gets a short-lived JWT scoped
to exactly one room, which is derived from a thread the caller already owns.

The principal is carried into the room as participant metadata so the voice
worker can act as the real user against the conversation API instead of falling
back to ``default_workspace_id`` / ``default_user_id`` (blocker B6 in
``docs/features/voice-agent/02_PHASED_PLAN.md``).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from seleric_swarm.conversations.contracts import Principal, Thread, ThreadStatus
from seleric_swarm.voice import room_name_for_thread

router = APIRouter(prefix="/v1/voice", tags=["voice"])


class VoiceTokenRequest(BaseModel):
    """Reuse an existing thread, or create one when ``thread_id`` is omitted."""

    thread_id: str | None = None
    title: str | None = Field(default=None, max_length=500)


class VoiceTokenResponse(BaseModel):
    url: str
    token: str
    room: str
    thread_id: str
    identity: str
    expires_in_s: int


def _runtime(request: Request) -> Any:
    provider = getattr(request.app.state, "runtime_provider", None)
    if not callable(provider):
        raise TypeError("conversation runtime provider is not configured")
    return provider()


def _authenticated_principal(request: Request) -> Principal:
    """Voice deliberately refuses the anonymous default principal.

    ``_principal`` in the conversations router accepts it, which is right for
    read paths in a single-tenant local run. Minting a room token for the
    default principal would hand a real audio channel to an unauthenticated
    caller, so this route requires a genuine identity.
    """

    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal):
        raise HTTPException(status_code=401, detail="principal unavailable")
    if not principal.authenticated:
        raise HTTPException(status_code=401, detail="authentication required")
    return principal


def _settings(runtime: Any) -> Any:
    settings = getattr(runtime, "settings", None)
    if settings is None:
        raise RuntimeError("runtime settings are not configured")
    return settings


def _repositories(runtime: Any) -> Any:
    repositories = getattr(runtime, "conversations", None)
    if repositories is None:
        raise RuntimeError("conversation repositories are not configured")
    return repositories


def _resolve_thread(repositories: Any, principal: Principal, body: VoiceTokenRequest) -> Thread:
    if body.thread_id is None:
        return repositories.threads.create(
            Thread(
                workspace_id=principal.workspace_id,
                owner_user_id=principal.user_id,
                title=body.title,
                metadata={"source": "voice"},
            )
        )
    thread = repositories.threads.get(
        body.thread_id, principal.workspace_id, principal.user_id
    )
    # Same 404-not-403 shape as _owned_thread: never confirm that a thread
    # exists in a workspace the caller cannot see.
    if (
        thread is None
        or not thread.is_owned_by(principal)
        or thread.status is not ThreadStatus.ACTIVE
    ):
        raise HTTPException(status_code=404, detail="thread not found")
    return thread


@router.post("/token", status_code=status.HTTP_201_CREATED)
def mint_voice_token(body: VoiceTokenRequest, request: Request) -> VoiceTokenResponse:
    runtime = _runtime(request)
    settings = _settings(runtime)
    if not getattr(settings, "voice_enabled", False):
        raise HTTPException(status_code=404, detail="voice is not enabled")
    # Settings construction deliberately does not enforce this (it would break
    # every unrelated entry point), so the check lands here instead of minting
    # a token against empty credentials.
    missing: list[str] = getattr(settings, "missing_voice_credentials", list)()
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"voice is misconfigured: missing {', '.join(missing)}",
        )

    principal = _authenticated_principal(request)
    thread = _resolve_thread(_repositories(runtime), principal, body)
    room = room_name_for_thread(thread.id)
    ttl_s = int(getattr(settings, "voice_token_ttl_s", 900))

    token = mint_room_jwt(
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
        identity=principal.user_id,
        room=room,
        ttl_s=ttl_s,
        metadata={
            "workspace_id": principal.workspace_id,
            "user_id": principal.user_id,
            "thread_id": thread.id,
        },
    )
    return VoiceTokenResponse(
        url=settings.livekit_url,
        token=token,
        room=room,
        thread_id=thread.id,
        identity=principal.user_id,
        expires_in_s=ttl_s,
    )


def mint_room_jwt(
    *,
    api_key: str,
    api_secret: str,
    identity: str,
    room: str,
    ttl_s: int,
    metadata: dict[str, str],
) -> str:
    """Build a LiveKit access token granting join on exactly one room.

    Imported lazily: ``livekit-api`` is the optional ``voice-api`` extra, and a
    deployment with voice switched off must not need it installed.
    """

    try:
        from livekit import api as livekit_api
    except ImportError as exc:  # pragma: no cover - exercised by the extra
        raise HTTPException(
            status_code=503,
            detail="voice token minting requires the 'voice-api' extra (livekit-api)",
        ) from exc

    from datetime import timedelta

    grants = livekit_api.VideoGrants(
        room_join=True,
        room=room,
        can_publish=True,
        can_subscribe=True,
        # The worker carries the principal; a participant must not be able to
        # inject arbitrary data messages into the room.
        can_publish_data=False,
    )
    return (
        livekit_api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_grants(grants)
        .with_metadata(json.dumps(metadata, separators=(",", ":")))
        .with_ttl(timedelta(seconds=ttl_s))
        .to_jwt()
    )
