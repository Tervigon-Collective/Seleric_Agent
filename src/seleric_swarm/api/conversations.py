"""Ownership-aware conversation HTTP API."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from starlette.responses import RedirectResponse, StreamingResponse

from seleric_swarm.api.async_missions import (
    cancel_running_mission,
    new_mission_id,
    run_mission_job,
    seed_running_mission,
)
from seleric_swarm.conversations.blobs import (
    BlobStore,
    BlobValidationError,
    LocalBlobStore,
    MalwareScanStatus,
)
from seleric_swarm.conversations.context import (
    ContextBuilder,
    MemoryCandidateExtractor,
    MemoryService,
    ThreadSummaryService,
    normalize_memory_content,
)
from seleric_swarm.conversations.contracts import (
    ActivityEvent,
    Artifact,
    ArtifactProvenance,
    Attachment,
    AttachmentStatus,
    MemoryItem,
    MemoryPreference,
    MemoryScope,
    MemoryStatus,
    MemoryType,
    Message,
    MessagePart,
    MessagePartType,
    MessageRole,
    Principal,
    Run,
    RunAttempt,
    RunAttemptStatus,
    RunStatus,
    Thread,
    ThreadStatus,
)
from seleric_swarm.conversations.events import ActivityEventSink, InMemoryEventNotifier
from seleric_swarm.conversations.privacy import event_for_principal
from seleric_swarm.conversations.repositories import ConversationRepositories
from seleric_swarm.runtime import SwarmRuntime

router = APIRouter(prefix="/v1", tags=["conversations"])


class CreateThreadRequest(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    project_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SubmitMessageRequest(BaseModel):
    parts: list[MessagePart] = Field(min_length=1)
    parent_message_id: str | None = None
    scope: dict[str, Any] = Field(default_factory=dict)
    execution_mode: str = "production"


class InitiateAttachmentRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0)


class CompleteAttachmentRequest(BaseModel):
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CreateMemoryRequest(BaseModel):
    content: str | dict[str, Any]
    scope: MemoryScope = MemoryScope.USER
    type: MemoryType = MemoryType.FACT
    project_id: str | None = None
    thread_id: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    salience: float = Field(default=0.5, ge=0, le=1)
    source_run_id: str | None = None
    source_message_ids: list[str] = Field(default_factory=list)
    source_evidence_ids: list[str] = Field(default_factory=list)
    pinned: bool = False


class UpdateMemoryRequest(BaseModel):
    content: str | dict[str, Any] | None = None
    type: MemoryType | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    salience: float | None = Field(default=None, ge=0, le=1)


class MoveMemoryRequest(BaseModel):
    scope: MemoryScope
    project_id: str | None = None
    thread_id: str | None = None


class MemoryPreferenceRequest(BaseModel):
    opted_out: bool


def _runtime(request: Request) -> SwarmRuntime:
    provider = getattr(request.app.state, "runtime_provider", None)
    if not callable(provider):
        raise TypeError("conversation runtime provider is not configured")
    return provider()


def _repositories(runtime: SwarmRuntime) -> ConversationRepositories:
    repositories = runtime.conversations
    if repositories is None:
        raise RuntimeError("conversation repositories are not configured")
    return repositories


def _execution_setting(runtime: SwarmRuntime, name: str, default: Any) -> Any:
    return getattr(getattr(runtime, "settings", None), name, default)


def _event_sink(
    runtime: SwarmRuntime, repositories: ConversationRepositories
) -> ActivityEventSink:
    sink = getattr(runtime, "activity_events", None)
    if isinstance(sink, ActivityEventSink):
        return sink
    sink = ActivityEventSink(repositories.runs, InMemoryEventNotifier())
    runtime.activity_events = sink
    return sink


def _principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal):
        raise HTTPException(status_code=401, detail="principal unavailable")
    return principal


def _owned_thread(
    repositories: ConversationRepositories, principal: Principal, thread_id: str
) -> Thread:
    thread = repositories.threads.get(thread_id)
    if thread is None or not thread.is_owned_by(principal):
        raise HTTPException(status_code=404, detail="thread not found")
    return thread


def _owned_run(
    repositories: ConversationRepositories, principal: Principal, run_id: str
) -> Run:
    run = repositories.runs.get(run_id)
    if run is None or not principal.owns(
        workspace_id=run.workspace_id, user_id=run.requested_by_user_id
    ):
        raise HTTPException(status_code=404, detail="run not found")
    return run


def _owned_attachment(
    repositories: ConversationRepositories, principal: Principal, attachment_id: str
) -> Attachment:
    attachment = repositories.attachments.get(attachment_id)
    if attachment is None or not principal.owns(
        workspace_id=attachment.workspace_id, user_id=attachment.owner_user_id
    ):
        raise HTTPException(status_code=404, detail="attachment not found")
    return attachment


def _owned_memory(
    repositories: ConversationRepositories, principal: Principal, memory_id: str
) -> MemoryItem:
    memory = repositories.memories.get(
        memory_id, principal.workspace_id, principal.user_id
    )
    if memory is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return memory


def _event_cursor(request: Request, after_sequence: int) -> int:
    value = (request.headers.get("last-event-id") or "").rsplit(":", 1)[-1]
    try:
        return max(after_sequence, int(value))
    except ValueError:
        return after_sequence


def _sse_event(event: ActivityEvent, principal: Principal) -> str | None:
    public = event_for_principal(event, principal)
    if public is None:
        return None
    data = public.model_dump(mode="json")
    data["type"] = public.event_type
    return (
        f"id: {public.sequence}\n"
        f"event: {public.event_type}\n"
        f"data: {json.dumps(data, separators=(',', ':'))}\n\n"
    )


def _stream_response(iterator: Any) -> StreamingResponse:
    return StreamingResponse(
        iterator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/threads", status_code=status.HTTP_201_CREATED)
def create_thread(body: CreateThreadRequest, request: Request) -> Thread:
    principal = _principal(request)
    repositories = _repositories(_runtime(request))
    thread = Thread(
        workspace_id=principal.workspace_id,
        owner_user_id=principal.user_id,
        title=body.title,
        project_id=body.project_id,
        metadata=body.metadata,
    )
    return repositories.threads.create(thread)


@router.get("/threads")
def list_threads(
    request: Request, limit: int = Query(default=50, ge=1, le=200)
) -> list[Thread]:
    principal = _principal(request)
    return _repositories(_runtime(request)).threads.list_for_owner(
        principal.workspace_id, principal.user_id, limit=limit
    )


@router.get("/threads/{thread_id}")
def get_thread(thread_id: str, request: Request) -> Thread:
    runtime = _runtime(request)
    return _owned_thread(_repositories(runtime), _principal(request), thread_id)


@router.post("/threads/{thread_id}/archive")
def archive_thread(thread_id: str, request: Request) -> Thread:
    runtime = _runtime(request)
    repositories = _repositories(runtime)
    thread = _owned_thread(repositories, _principal(request), thread_id)
    archived = thread.model_copy(
        update={"status": ThreadStatus.ARCHIVED, "updated_at": datetime.now(UTC)}
    )
    return repositories.threads.update(archived)


@router.get("/threads/{thread_id}/messages")
def list_messages(
    thread_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[Message]:
    runtime = _runtime(request)
    repositories = _repositories(runtime)
    _owned_thread(repositories, _principal(request), thread_id)
    return repositories.messages.list_for_thread(thread_id, limit=limit)


@router.get("/memories")
def list_memories(
    request: Request,
    project_id: str | None = None,
    thread_id: str | None = None,
    include_inactive: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[MemoryItem]:
    principal = _authenticated_principal(request)
    repositories = _repositories(_runtime(request))
    if thread_id is not None:
        _owned_thread(repositories, principal, thread_id)
    return repositories.memories.list(
        principal.workspace_id,
        principal.user_id,
        project_id=project_id,
        thread_id=thread_id,
        include_inactive=include_inactive,
        limit=limit,
    )


@router.post("/memories", status_code=status.HTTP_201_CREATED)
def create_memory(body: CreateMemoryRequest, request: Request) -> MemoryItem:
    principal = _authenticated_principal(request)
    repositories = _repositories(_runtime(request))
    if body.scope is MemoryScope.THREAD and body.thread_id is None:
        raise HTTPException(status_code=422, detail="THREAD scope requires thread_id")
    if body.scope in {MemoryScope.PROJECT, MemoryScope.EPISODIC} and body.project_id is None:
        raise HTTPException(status_code=422, detail="PROJECT/EPISODIC scope requires project_id")
    if body.thread_id is not None:
        _owned_thread(repositories, principal, body.thread_id)
    if body.source_run_id is not None:
        _owned_run(repositories, principal, body.source_run_id)
    for message_id in body.source_message_ids:
        source_message = repositories.messages.get(message_id)
        if source_message is None:
            raise HTTPException(status_code=422, detail="source message not found")
        _owned_thread(repositories, principal, source_message.thread_id)
    if repositories.memories.get_preference(
        principal.workspace_id, principal.user_id
    ).opted_out:
        raise HTTPException(status_code=409, detail="memory is opted out")
    now = datetime.now(UTC)
    return repositories.memories.create(
        MemoryItem(
            workspace_id=principal.workspace_id,
            owner_user_id=principal.user_id,
            project_id=body.project_id,
            thread_id=body.thread_id,
            scope=body.scope,
            type=body.type,
            status=MemoryStatus.ACTIVE,
            content=body.content,
            normalized_content=normalize_memory_content(body.content),
            provenance={"created_by": "user"},
            confidence=body.confidence,
            salience=body.salience,
            source_run_id=body.source_run_id,
            source_message_ids=body.source_message_ids,
            source_evidence_ids=body.source_evidence_ids,
            pinned=body.pinned,
            requires_confirmation=False,
            consented_at=now,
        )
    )


@router.get("/memories/preference")
def get_memory_preference(request: Request) -> MemoryPreference:
    principal = _authenticated_principal(request)
    return _repositories(_runtime(request)).memories.get_preference(
        principal.workspace_id, principal.user_id
    )


@router.put("/memories/preference")
def set_memory_preference(
    body: MemoryPreferenceRequest, request: Request
) -> MemoryPreference:
    principal = _authenticated_principal(request)
    return _repositories(_runtime(request)).memories.set_preference(
        MemoryPreference(
            workspace_id=principal.workspace_id,
            owner_user_id=principal.user_id,
            opted_out=body.opted_out,
        )
    )


@router.get("/memories/{memory_id}")
def get_memory(memory_id: str, request: Request) -> MemoryItem:
    return _owned_memory(
        _repositories(_runtime(request)), _authenticated_principal(request), memory_id
    )


@router.patch("/memories/{memory_id}")
def update_memory(
    memory_id: str, body: UpdateMemoryRequest, request: Request
) -> MemoryItem:
    principal = _authenticated_principal(request)
    repositories = _repositories(_runtime(request))
    memory = _owned_memory(repositories, principal, memory_id)
    changes = body.model_dump(exclude_unset=True)
    if "content" in changes:
        if changes["content"] is None:
            raise HTTPException(status_code=422, detail="content must not be null")
        changes["normalized_content"] = normalize_memory_content(changes["content"])
    changes["updated_at"] = datetime.now(UTC)
    return repositories.memories.update(memory.model_copy(update=changes))


@router.post("/memories/{memory_id}/pin")
def pin_memory(memory_id: str, request: Request, pinned: bool = True) -> MemoryItem:
    principal = _authenticated_principal(request)
    repositories = _repositories(_runtime(request))
    memory = _owned_memory(repositories, principal, memory_id)
    return repositories.memories.update(
        memory.model_copy(update={"pinned": pinned, "updated_at": datetime.now(UTC)})
    )


@router.post("/memories/{memory_id}/confirm")
def confirm_memory(memory_id: str, request: Request) -> MemoryItem:
    principal = _authenticated_principal(request)
    repositories = _repositories(_runtime(request))
    memory = _owned_memory(repositories, principal, memory_id)
    return MemoryService(repositories).confirm(memory)


@router.post("/memories/{memory_id}/move")
def move_memory(
    memory_id: str, body: MoveMemoryRequest, request: Request
) -> MemoryItem:
    principal = _authenticated_principal(request)
    repositories = _repositories(_runtime(request))
    memory = _owned_memory(repositories, principal, memory_id)
    if body.scope is MemoryScope.THREAD and body.thread_id is None:
        raise HTTPException(status_code=422, detail="THREAD scope requires thread_id")
    if body.scope in {MemoryScope.PROJECT, MemoryScope.EPISODIC} and body.project_id is None:
        raise HTTPException(status_code=422, detail="PROJECT/EPISODIC scope requires project_id")
    if body.thread_id is not None:
        _owned_thread(repositories, principal, body.thread_id)
    moved = memory.model_copy(
        update={
            "scope": body.scope,
            "project_id": body.project_id,
            "thread_id": body.thread_id,
            "updated_at": datetime.now(UTC),
        }
    )
    return repositories.memories.update(moved)


@router.post("/memories/{memory_id}/archive")
def archive_memory(memory_id: str, request: Request) -> MemoryItem:
    principal = _authenticated_principal(request)
    repositories = _repositories(_runtime(request))
    memory = _owned_memory(repositories, principal, memory_id)
    now = datetime.now(UTC)
    return repositories.memories.update(
        memory.model_copy(
            update={"status": MemoryStatus.ARCHIVED, "archived_at": now, "updated_at": now}
        )
    )


@router.delete("/memories/{memory_id}")
def delete_memory(memory_id: str, request: Request) -> dict[str, str]:
    principal = _authenticated_principal(request)
    repositories = _repositories(_runtime(request))
    _owned_memory(repositories, principal, memory_id)
    repositories.memories.delete(memory_id, principal.workspace_id, principal.user_id)
    return {"memory_id": memory_id, "status": MemoryStatus.DELETED.value}


@router.get("/runs/{run_id}/memories")
def list_run_memories(run_id: str, request: Request) -> list[MemoryItem]:
    principal = _authenticated_principal(request)
    repositories = _repositories(_runtime(request))
    _owned_run(repositories, principal, run_id)
    return repositories.memories.list_used_by_run(
        run_id, principal.workspace_id, principal.user_id
    )


@router.post(
    "/threads/{thread_id}/attachments",
    status_code=status.HTTP_201_CREATED,
)
def initiate_attachment(
    thread_id: str, body: InitiateAttachmentRequest, request: Request
) -> dict[str, Any]:
    runtime = _runtime(request)
    repositories = _repositories(runtime)
    principal = _authenticated_principal(request)
    thread = _owned_thread(repositories, principal, thread_id)
    blob_store = getattr(runtime, "blob_store", None)
    if blob_store is None:
        raise HTTPException(status_code=503, detail="blob storage is not configured")
    max_size = int(getattr(blob_store, "max_size_bytes", 0))
    if max_size and body.size_bytes > max_size:
        raise HTTPException(status_code=413, detail="attachment exceeds configured maximum size")
    if isinstance(blob_store, LocalBlobStore):
        try:
            blob_store.validate_metadata(body.filename, body.content_type, body.size_bytes)
        except BlobValidationError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
    attachment = repositories.attachments.create(
        Attachment(
            thread_id=thread.id,
            workspace_id=thread.workspace_id,
            owner_user_id=principal.user_id,
            filename=body.filename,
            content_type=body.content_type.split(";", 1)[0].lower(),
            size_bytes=body.size_bytes,
        )
    )
    return {
        "attachment": attachment.model_dump(mode="json"),
        "upload": {
            "method": "PUT",
            "url": blob_store.presign_upload(
                attachment.id, content_type=attachment.content_type
            ),
        },
    }


@router.get("/threads/{thread_id}/attachments")
def list_attachments(thread_id: str, request: Request) -> list[Attachment]:
    runtime = _runtime(request)
    repositories = _repositories(runtime)
    _owned_thread(repositories, _authenticated_principal(request), thread_id)
    return repositories.attachments.list_for_thread(thread_id)


@router.put("/attachments/{attachment_id}/content")
async def upload_attachment_content(attachment_id: str, request: Request) -> Attachment:
    runtime = _runtime(request)
    repositories = _repositories(runtime)
    principal = _authenticated_principal(request)
    attachment = _owned_attachment(repositories, principal, attachment_id)
    if attachment.status is not AttachmentStatus.PENDING:
        raise HTTPException(status_code=409, detail="attachment is not pending")
    blob_store = getattr(runtime, "blob_store", None)
    if not isinstance(blob_store, LocalBlobStore):
        raise HTTPException(status_code=405, detail="use the object-store presigned URL")
    request_type = (request.headers.get("content-type") or "").split(";", 1)[0].lower()
    if request_type and request_type != attachment.content_type:
        raise HTTPException(status_code=415, detail="Content-Type does not match declaration")
    try:
        result = await blob_store.put(
            attachment.id,
            request.stream(),
            content_type=attachment.content_type,
            expected_size=attachment.size_bytes,
        )
    except BlobValidationError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    final_status = (
        AttachmentStatus.READY
        if result.scan.status is MalwareScanStatus.CLEAN
        else AttachmentStatus.FAILED
    )
    return repositories.attachments.update(
        attachment.model_copy(
            update={
                "size_bytes": result.size_bytes,
                "storage_uri": result.uri,
                "checksum_sha256": result.checksum_sha256,
                "status": final_status,
            }
        )
    )


@router.post("/attachments/{attachment_id}/complete")
def complete_presigned_upload(
    attachment_id: str, body: CompleteAttachmentRequest, request: Request
) -> Attachment:
    runtime = _runtime(request)
    repositories = _repositories(runtime)
    attachment = _owned_attachment(
        repositories, _authenticated_principal(request), attachment_id
    )
    blob_store = getattr(runtime, "blob_store", None)
    if isinstance(blob_store, LocalBlobStore):
        raise HTTPException(status_code=409, detail="local uploads complete with the PUT request")
    if blob_store is None or not hasattr(blob_store, "client"):
        raise HTTPException(status_code=503, detail="object storage is not configured")
    stat = blob_store.client.stat_object(blob_store.bucket, attachment.id)
    if int(stat.size) != attachment.size_bytes:
        raise HTTPException(status_code=409, detail="object size does not match declaration")
    metadata = {str(k).lower(): str(v) for k, v in (stat.metadata or {}).items()}
    stored_checksum = metadata.get("x-amz-meta-sha256") or metadata.get("sha256")
    if stored_checksum and stored_checksum != body.checksum_sha256:
        raise HTTPException(status_code=409, detail="object checksum metadata does not match")
    return repositories.attachments.update(
        attachment.model_copy(
            update={
                "storage_uri": f"s3://{blob_store.bucket}/{attachment.id}",
                "checksum_sha256": body.checksum_sha256,
                "status": AttachmentStatus.READY,
            }
        )
    )


@router.get("/attachments/{attachment_id}")
def get_attachment(attachment_id: str, request: Request) -> Attachment:
    return _owned_attachment(
        _repositories(_runtime(request)), _authenticated_principal(request), attachment_id
    )


@router.get("/attachments/{attachment_id}/download")
def download_attachment(attachment_id: str, request: Request):
    runtime = _runtime(request)
    attachment = _owned_attachment(
        _repositories(runtime), _authenticated_principal(request), attachment_id
    )
    if attachment.status is not AttachmentStatus.READY:
        raise HTTPException(status_code=409, detail="attachment is not ready")
    blob_store = cast(BlobStore, getattr(runtime, "blob_store", None))
    if isinstance(blob_store, LocalBlobStore):
        return StreamingResponse(
            blob_store.iter_bytes(attachment.id),
            media_type=attachment.content_type,
            headers={"Content-Disposition": f'attachment; filename="{attachment.filename}"'},
        )
    return RedirectResponse(blob_store.presign_download(attachment.id), status_code=307)


@router.get("/attachments/{attachment_id}/presigned-download")
def presigned_download(attachment_id: str, request: Request) -> dict[str, str]:
    runtime = _runtime(request)
    attachment = _owned_attachment(
        _repositories(runtime), _authenticated_principal(request), attachment_id
    )
    if attachment.status is not AttachmentStatus.READY:
        raise HTTPException(status_code=409, detail="attachment is not ready")
    blob_store = getattr(runtime, "blob_store", None)
    if blob_store is None:
        raise HTTPException(status_code=503, detail="blob storage is not configured")
    return {"url": blob_store.presign_download(attachment.id)}


@router.delete("/attachments/{attachment_id}")
def delete_attachment(attachment_id: str, request: Request) -> dict[str, str]:
    runtime = _runtime(request)
    repositories = _repositories(runtime)
    attachment = _owned_attachment(
        repositories, _authenticated_principal(request), attachment_id
    )
    blob_store = getattr(runtime, "blob_store", None)
    if blob_store is not None:
        blob_store.delete(attachment.id)
    repositories.attachments.delete(attachment.id)
    return {"attachment_id": attachment.id, "status": AttachmentStatus.DELETED.value}


def _authenticated_principal(request: Request) -> Principal:
    principal = _principal(request)
    if not principal.authenticated:
        raise HTTPException(status_code=401, detail="authentication required")
    return principal


@router.get("/runs/{run_id}/events")
def list_run_events(
    run_id: str,
    request: Request,
    after_sequence: int = Query(default=0, ge=0),
) -> list[ActivityEvent]:
    runtime = _runtime(request)
    repositories = _repositories(runtime)
    principal = _authenticated_principal(request)
    _owned_run(repositories, principal, run_id)
    return [
        public
        for event in repositories.runs.list_events(run_id, after_sequence=after_sequence)
        if (public := event_for_principal(event, principal)) is not None
    ]


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, request: Request) -> dict[str, str]:
    runtime = _runtime(request)
    repositories = _repositories(runtime)
    principal = _authenticated_principal(request)
    run = _owned_run(repositories, principal, run_id)
    if not repositories.runs.cancel(run_id):
        raise HTTPException(status_code=409, detail="run is not cancellable")
    cancellation = getattr(runtime, "cancellation", None)
    if cancellation is not None and run.mission_id:
        cancellation.request(run.mission_id)
    if run.mission_id:
        try:
            cancel_running_mission(runtime, mission_id=run.mission_id)
        except (KeyError, ValueError):
            pass
    return {"run_id": run_id, "status": RunStatus.CANCELLED.value}


@router.get("/threads/{thread_id}/events")
def list_thread_events(
    thread_id: str,
    request: Request,
    after_sequence: int = Query(default=0, ge=0),
) -> list[ActivityEvent]:
    runtime = _runtime(request)
    repositories = _repositories(runtime)
    principal = _authenticated_principal(request)
    _owned_thread(repositories, principal, thread_id)
    return [
        public
        for event in repositories.runs.list_thread_events(
            thread_id, after_sequence=after_sequence
        )
        if (public := event_for_principal(event, principal)) is not None
    ]


@router.get("/runs/{run_id}/events/stream")
async def stream_run_events(
    run_id: str,
    request: Request,
    after_sequence: int = Query(default=0, ge=0),
    heartbeat_seconds: float = Query(default=15.0, ge=0.05, le=60.0),
) -> StreamingResponse:
    runtime = _runtime(request)
    repositories = _repositories(runtime)
    principal = _authenticated_principal(request)
    _owned_run(repositories, principal, run_id)
    sink = _event_sink(runtime, repositories)
    cursor = _event_cursor(request, after_sequence)

    async def generate():
        nonlocal cursor
        while True:
            events = repositories.runs.list_events(run_id, after_sequence=cursor)
            for event in events:
                cursor = max(cursor, event.sequence)
                encoded = _sse_event(event, principal)
                if encoded is not None:
                    yield encoded
            run = repositories.runs.get(run_id)
            if run is None or run.status in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                return
            if await request.is_disconnected():
                return
            yield ": heartbeat\n\n"
            await sink.notifier.wait(run_id, cursor, heartbeat_seconds)

    return _stream_response(generate())


@router.get("/threads/{thread_id}/events/stream")
async def stream_thread_events(
    thread_id: str,
    request: Request,
    after_sequence: int = Query(default=0, ge=0),
    heartbeat_seconds: float = Query(default=15.0, ge=0.05, le=60.0),
) -> StreamingResponse:
    runtime = _runtime(request)
    repositories = _repositories(runtime)
    principal = _authenticated_principal(request)
    _owned_thread(repositories, principal, thread_id)
    cursor = _event_cursor(request, after_sequence)

    async def generate():
        nonlocal cursor
        while True:
            events = repositories.runs.list_thread_events(
                thread_id, after_sequence=cursor
            )
            for event in events:
                cursor = max(cursor, event.sequence)
                encoded = _sse_event(event, principal)
                if encoded is not None:
                    yield encoded
            if await request.is_disconnected():
                return
            yield ": heartbeat\n\n"
            await asyncio.sleep(heartbeat_seconds)

    return _stream_response(generate())


async def _execute_submission(
    runtime: SwarmRuntime,
    repositories: ConversationRepositories,
    *,
    run: Run,
    attempt: RunAttempt,
    query: str,
    timezone: str,
    as_of: str | None,
    request_id: str,
    execution_mode: str,
) -> None:
    sink = _event_sink(runtime, repositories)
    worker_id = _execution_setting(runtime, "run_worker_id", "") or f"worker-{uuid4().hex[:12]}"
    claimed = repositories.runs.claim(
        run.id, worker_id, _execution_setting(runtime, "run_lease_s", 60.0)
    )
    if claimed is None:
        return
    attempt = claimed
    running = run.model_copy(update={"status": RunStatus.RUNNING, "started_at": datetime.now(UTC)})
    repositories.runs.update(running)
    sink.emit(
        running,
        "run.started",
        payload={"mission_id": run.mission_id},
        started_at=running.started_at,
    )
    stop_heartbeat = asyncio.Event()

    async def _heartbeat() -> None:
        while not stop_heartbeat.is_set():
            try:
                await asyncio.wait_for(
                    stop_heartbeat.wait(),
                    timeout=_execution_setting(runtime, "run_heartbeat_s", 15.0),
                )
            except TimeoutError:
                if not repositories.runs.heartbeat(
                    attempt.id,
                    worker_id,
                    _execution_setting(runtime, "run_lease_s", 60.0),
                ):
                    return

    heartbeat_task = asyncio.create_task(_heartbeat())
    try:
        await run_mission_job(
            runtime,
            mission_id=run.mission_id or "",
            query=query,
            timezone=timezone,
            as_of=as_of,
            session_id=run.thread_id,
            request_id=request_id,
            full_diagnostic=True,
            full_prediction=True,
            full_skeptic=True,
            full_strategy=True,
            execution_mode=execution_mode,
        )
    finally:
        stop_heartbeat.set()
        await heartbeat_task
    raw = getattr(runtime.store, "get_raw", lambda _mission_id: None)(run.mission_id)
    raw = raw if isinstance(raw, dict) else {}
    context_bundle = run.metadata.get("context_bundle")
    if isinstance(context_bundle, dict):
        raw = {
            **raw,
            "metadata": {**(raw.get("metadata") or {}), "context_bundle": context_bundle},
            "input": {"query": query, "context_bundle": context_bundle},
        }
        persisted_result = runtime.store.get(run.mission_id or "")
        if persisted_result is not None:
            runtime.store.put(persisted_result, raw)
    mission_events = raw.get("events")
    if isinstance(mission_events, list):
        sink.ingest_mission_events(
            running,
            [event for event in mission_events if isinstance(event, dict)],
            exclude_event_types={"run.started", "run.completed", "run.failed"},
        )
    mission_status = raw.get("status")
    final_status = {
        "failed": RunStatus.FAILED,
        "cancelled": RunStatus.CANCELLED,
    }.get(str(mission_status), RunStatus.COMPLETED)
    persisted_run = repositories.runs.get(run.id)
    if persisted_run is not None and persisted_run.status is RunStatus.CANCELLED:
        final_status = RunStatus.CANCELLED
    attempt_status = {
        RunStatus.FAILED: RunAttemptStatus.FAILED,
        RunStatus.CANCELLED: RunAttemptStatus.CANCELLED,
    }.get(final_status, RunAttemptStatus.COMPLETED)
    completed_at = datetime.now(UTC)
    final_response = raw.get("final_response")
    if isinstance(final_response, str) and final_response.strip():
        final_message = repositories.messages.create(
            Message(
                thread_id=run.thread_id,
                workspace_id=run.workspace_id,
                role=MessageRole.ASSISTANT,
                run_id=run.id,
                parts=[MessagePart(type=MessagePartType.TEXT, content=final_response)],
            )
        )
        sink.emit(
            running,
            "answer.completed",
            payload={"message_id": final_message.id, "mission_id": run.mission_id},
        )
        summary_thread = repositories.threads.get(run.thread_id)
        if summary_thread is not None:
            ThreadSummaryService(repositories).create(
                summary_thread,
                repositories.messages.list_for_thread(run.thread_id, limit=500),
                mode="deterministic",
            )
    artifact_groups = raw.get("artifacts")
    if isinstance(artifact_groups, dict):
        for artifact_type, items in artifact_groups.items():
            for payload in items if isinstance(items, list) else []:
                if not isinstance(payload, dict):
                    continue
                evidence_ids = [
                    str(item)
                    for item in (
                        payload.get("evidence_refs")
                        or payload.get("evidence_ids")
                        or payload.get("source_evidence_ids")
                        or []
                    )
                    if item
                ]
                if str(artifact_type).lower() == "evidence":
                    own_id = payload.get("evidence_id") or payload.get("artifact_id")
                    if own_id:
                        evidence_ids.append(str(own_id))
                source_metadata = payload.get("source_metadata")
                if not isinstance(source_metadata, dict):
                    source_metadata = {
                        key: payload[key]
                        for key in ("source", "source_tool", "query", "dataset", "time_range")
                        if key in payload
                    }
                classification: Literal["ui", "factual", "derived"] = (
                    "factual"
                    if str(artifact_type).lower() == "evidence"
                    else "derived"
                    if evidence_ids
                    else "ui"
                )
                artifact = repositories.artifacts.put(
                    Artifact(
                        id=str(
                            payload.get("artifact_id")
                            or payload.get("id")
                            or f"artifact_{uuid4().hex}"
                        ),
                        workspace_id=run.workspace_id,
                        artifact_type=str(artifact_type),
                        payload=payload,
                        classification=classification,
                        evidence_ids=list(dict.fromkeys(evidence_ids)),
                        provenance=ArtifactProvenance(
                            evidence_ids=list(dict.fromkeys(evidence_ids)),
                            calculation_version=str(
                                payload.get("calculation_version")
                                or _execution_setting(runtime, "workflow_version", "unknown")
                            ) if classification == "derived" else None,
                            query_version=(
                                str(payload["query_version"])
                                if payload.get("query_version") is not None
                                else None
                            ),
                            prompt_version=(
                                str(payload["prompt_version"])
                                if payload.get("prompt_version") is not None
                                else None
                            ),
                            tool_version=(
                                str(payload["tool_version"])
                                if payload.get("tool_version") is not None
                                else None
                            ),
                            model_version=(
                                str(payload["model_version"])
                                if payload.get("model_version") is not None
                                else None
                            ),
                            source_metadata=source_metadata,
                        ),
                        mission_id=run.mission_id,
                        thread_id=run.thread_id,
                        run_id=run.id,
                    )
                )
                sink.emit(
                    running,
                    "artifact.created",
                    payload={
                        "artifact_id": artifact.id,
                        "artifact_type": artifact.artifact_type,
                    },
                )
    sink.emit(
        running,
        "run.completed" if final_status is RunStatus.COMPLETED else "run.failed",
        payload={"mission_id": run.mission_id, "status": final_status.value.lower()},
        completed_at=completed_at,
    )
    repositories.runs.update(
        running.model_copy(update={"status": final_status, "completed_at": completed_at})
    )
    repositories.runs.compare_and_set_attempt(
        attempt.id,
        RunAttemptStatus.RUNNING,
        attempt_status,
        worker_id=worker_id,
        now=completed_at,
    )


@router.post(
    "/threads/{thread_id}/messages",
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_message(
    thread_id: str,
    body: SubmitMessageRequest,
    background_tasks: BackgroundTasks,
    request: Request,
) -> dict[str, str]:
    runtime = _runtime(request)
    repositories = _repositories(runtime)
    principal = _principal(request)
    thread = _owned_thread(repositories, principal, thread_id)
    if thread.status is not ThreadStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="thread is not active")
    if body.execution_mode not in {"staging", "production"}:
        raise HTTPException(status_code=400, detail="invalid execution_mode")
    query = "\n".join(
        str(part.content).strip()
        for part in body.parts
        if part.type.value == "TEXT" and isinstance(part.content, str) and part.content.strip()
    )
    if not query:
        raise HTTPException(status_code=400, detail="at least one non-empty TEXT part is required")

    mission_id = new_mission_id()
    run = Run(
        thread_id=thread.id,
        workspace_id=thread.workspace_id,
        requested_by_user_id=principal.user_id,
        mission_id=mission_id,
        current_attempt=1,
        max_attempts=_execution_setting(runtime, "run_max_attempts", 3),
    )
    request_id = str(getattr(request.state, "request_id", "") or run.id)
    seeded = seed_running_mission(
        runtime,
        mission_id=mission_id,
        query=query,
        request_id=request_id,
        session_id=thread.id,
        workspace_id=thread.workspace_id,
        owner_user_id=thread.owner_user_id,
        thread_id=thread.id,
        run_id=run.id,
    )
    run = repositories.runs.create(run)
    attempt = repositories.runs.add_attempt(RunAttempt(run_id=run.id, attempt_number=1))
    message = repositories.messages.create(
        Message(
            thread_id=thread.id,
            workspace_id=thread.workspace_id,
            user_id=principal.user_id,
            role=MessageRole.USER,
            parts=body.parts,
            run_id=run.id,
            parent_message_id=body.parent_message_id,
        )
    )
    context_bundle = ContextBuilder(repositories).build(
        thread,
        query=query,
        workspace_config=(
            body.scope.get("workspace_config")
            if isinstance(body.scope.get("workspace_config"), dict)
            else {}
        ),
    )
    bundle_data = context_bundle.model_dump(mode="json")
    run = repositories.runs.update(
        run.model_copy(update={"metadata": {**run.metadata, "context_bundle": bundle_data}})
    )
    repositories.memories.record_usage(
        run.id,
        context_bundle.memories,
        {"thread_id": thread.id, "mission_id": mission_id, "builder": "phase6-v1"},
    )
    mission_result = runtime.store.get(mission_id)
    if mission_result is not None:
        runtime.store.put(
            mission_result,
            {
                **seeded,
                "metadata": {"context_bundle": bundle_data},
                "input": {"query": query, "context_bundle": bundle_data},
            },
        )
    MemoryService(repositories).ingest_candidates(
        MemoryCandidateExtractor().extract(message, thread)
    )
    repositories.threads.update(thread.model_copy(update={"updated_at": datetime.now(UTC)}))
    _event_sink(runtime, repositories).emit(
        run,
        "run.queued",
        payload={"message_id": message.id, "mission_id": mission_id},
        actor_user_id=principal.user_id,
    )
    background_tasks.add_task(
        _execute_submission,
        runtime,
        repositories,
        run=run,
        attempt=attempt,
        query=query,
        timezone=str(body.scope.get("timezone") or "Asia/Kolkata"),
        as_of=body.scope.get("as_of") or body.scope.get("asOf"),
        request_id=request_id,
        execution_mode=body.execution_mode,
    )
    return {"message_id": message.id, "run_id": run.id, "mission_id": mission_id}
