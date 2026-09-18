"""Ownership-aware conversation HTTP API."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, NoReturn, cast
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
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
    MinioBlobStore,
    normalize_content_type,
    validate_blob_metadata,
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
    AttachmentScanStatus,
    AttachmentStatus,
    MemoryItem,
    MemoryPreference,
    MemoryScope,
    MemoryStatus,
    MemoryType,
    Message,
    MessagePage,
    MessagePart,
    MessagePartType,
    MessageRole,
    Principal,
    Run,
    RunAttempt,
    RunAttemptStatus,
    RunStatus,
    Thread,
    ThreadPage,
    ThreadStatus,
)
from seleric_swarm.conversations.events import ActivityEventSink, InMemoryEventNotifier
from seleric_swarm.conversations.privacy import event_for_principal
from seleric_swarm.conversations.repositories import ConversationRepositories
from seleric_swarm.recovery import (
    InProcessRunQueue,
    RunExecutionResult,
    RunRecoveryWorker,
    RunWorkQueue,
)
from seleric_swarm.runtime import SwarmRuntime
from seleric_swarm.swarm.blackboard import observe_mission_events

router = APIRouter(prefix="/v1", tags=["conversations"])
ArtifactClassification = Literal["ui", "factual", "derived"]


class CreateThreadRequest(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    project_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateThreadRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)


class SubmitMessageRequest(BaseModel):
    parts: list[MessagePart] = Field(min_length=1)
    parent_message_id: str | None = None
    scope: dict[str, Any] = Field(default_factory=dict)
    execution_mode: str = "production"
    attachment_ids: list[str] = Field(default_factory=list)


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
    repositories: ConversationRepositories,
    principal: Principal,
    thread_id: str,
    *,
    allow_deleted: bool = False,
) -> Thread:
    thread = repositories.threads.get(
        thread_id, principal.workspace_id, principal.user_id
    )
    if (
        thread is None
        or not thread.is_owned_by(principal)
        or (thread.status is ThreadStatus.DELETED and not allow_deleted)
    ):
        raise HTTPException(status_code=404, detail="thread not found")
    return thread


def _owned_run(
    repositories: ConversationRepositories, principal: Principal, run_id: str
) -> Run:
    run = repositories.runs.get(
        run_id, principal.workspace_id, principal.user_id
    )
    if run is None or not principal.owns(
        workspace_id=run.workspace_id, user_id=run.requested_by_user_id
    ):
        raise HTTPException(status_code=404, detail="run not found")
    return run


def _owned_attachment(
    repositories: ConversationRepositories, principal: Principal, attachment_id: str
) -> Attachment:
    attachment = repositories.attachments.get(
        attachment_id, principal.workspace_id, principal.user_id
    )
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


def _sse_event(
    event: ActivityEvent, principal: Principal, *, cursor: int | None = None
) -> str | None:
    public = event_for_principal(event, principal)
    if public is None:
        return None
    data = public.model_dump(mode="json")
    data["type"] = public.event_type
    return (
        f"id: {cursor if cursor is not None else public.sequence}\n"
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
    threads = _repositories(_runtime(request)).threads.list_for_owner(
        principal.workspace_id,
        principal.user_id,
        limit=limit,
        status=ThreadStatus.ACTIVE.value,
    )
    return threads


@router.get("/threads/paginated")
def list_threads_paginated(
    request: Request,
    response: Response,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    include_deleted: bool = False,
) -> ThreadPage:
    principal = _principal(request)
    repositories = _repositories(_runtime(request))
    try:
        items, next_cursor = repositories.threads.list_page(
            principal.workspace_id,
            principal.user_id,
            limit=limit,
            cursor=cursor,
            include_deleted=include_deleted,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if next_cursor:
        response.headers["X-Next-Cursor"] = next_cursor
    return ThreadPage(items=items, next_cursor=next_cursor, has_more=next_cursor is not None)


@router.get("/threads/{thread_id}")
def get_thread(thread_id: str, request: Request) -> Thread:
    runtime = _runtime(request)
    return _owned_thread(_repositories(runtime), _principal(request), thread_id)


@router.patch("/threads/{thread_id}")
def update_thread(
    thread_id: str, body: UpdateThreadRequest, request: Request
) -> Thread:
    runtime = _runtime(request)
    repositories = _repositories(runtime)
    thread = _owned_thread(repositories, _principal(request), thread_id)
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="title must not be blank")
    updated = thread.model_copy(
        update={"title": title, "updated_at": datetime.now(UTC)}
    )
    return repositories.threads.update(updated)


@router.post("/threads/{thread_id}/archive")
def archive_thread(thread_id: str, request: Request) -> Thread:
    runtime = _runtime(request)
    repositories = _repositories(runtime)
    thread = _owned_thread(repositories, _principal(request), thread_id)
    archived = thread.model_copy(
        update={"status": ThreadStatus.ARCHIVED, "updated_at": datetime.now(UTC)}
    )
    return repositories.threads.update(archived)


@router.delete("/threads/{thread_id}")
def delete_thread(thread_id: str, request: Request) -> Thread:
    repositories = _repositories(_runtime(request))
    thread = _owned_thread(repositories, _principal(request), thread_id)
    now = datetime.now(UTC)
    return repositories.threads.update(
        thread.model_copy(
            update={"status": ThreadStatus.DELETED, "deleted_at": now, "updated_at": now}
        )
    )


@router.post("/threads/{thread_id}/restore")
def restore_thread(thread_id: str, request: Request) -> Thread:
    repositories = _repositories(_runtime(request))
    thread = _owned_thread(
        repositories, _principal(request), thread_id, allow_deleted=True
    )
    if thread.status is not ThreadStatus.DELETED:
        raise HTTPException(status_code=409, detail="thread is not deleted")
    return repositories.threads.update(
        thread.model_copy(
            update={
                "status": ThreadStatus.ACTIVE,
                "deleted_at": None,
                "updated_at": datetime.now(UTC),
            }
        )
    )


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


@router.get("/threads/{thread_id}/messages/paginated")
def list_messages_paginated(
    thread_id: str,
    request: Request,
    response: Response,
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = None,
) -> MessagePage:
    repositories = _repositories(_runtime(request))
    _owned_thread(repositories, _principal(request), thread_id)
    try:
        items, next_cursor = repositories.messages.list_page(
            thread_id, limit=limit, cursor=cursor
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if next_cursor:
        response.headers["X-Next-Cursor"] = next_cursor
    return MessagePage(items=items, next_cursor=next_cursor, has_more=next_cursor is not None)


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
    try:
        content_type = validate_blob_metadata(
            body.filename,
            body.content_type,
            body.size_bytes,
            max_size_bytes=int(blob_store.max_size_bytes),
            allowed_mime_types=blob_store.allowed_mime_types,
        )
    except BlobValidationError as exc:
        status_code = 413 if "size" in str(exc).lower() else 415
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    attachment = repositories.attachments.create(
        Attachment(
            thread_id=thread.id,
            workspace_id=thread.workspace_id,
            owner_user_id=principal.user_id,
            filename=body.filename,
            content_type=content_type,
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
                "scan_status": AttachmentScanStatus(result.scan.status.value),
                "scan_detail": result.scan.detail,
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
    if attachment.status is not AttachmentStatus.PENDING:
        raise HTTPException(status_code=409, detail="attachment is not pending")
    blob_store = getattr(runtime, "blob_store", None)
    if isinstance(blob_store, LocalBlobStore):
        raise HTTPException(status_code=409, detail="local uploads complete with the PUT request")
    if not isinstance(blob_store, MinioBlobStore):
        raise HTTPException(status_code=503, detail="object storage is not configured")

    def reject_completion(detail: str, checksum: str | None = None) -> NoReturn:
        blob_store.delete(attachment.id)
        repositories.attachments.update(
            attachment.model_copy(
                update={
                    "storage_uri": None,
                    "checksum_sha256": checksum,
                    "status": AttachmentStatus.FAILED,
                    "scan_status": AttachmentScanStatus.FAILED,
                    "scan_detail": detail,
                }
            )
        )
        raise HTTPException(status_code=409, detail=detail)

    stat = blob_store.client.stat_object(blob_store.bucket, attachment.id)
    if int(stat.size) != attachment.size_bytes:
        reject_completion("object size does not match declaration")
    try:
        stored_type = normalize_content_type(str(stat.content_type or ""))
    except BlobValidationError:
        reject_completion("stored object MIME type is invalid")
    if stored_type != attachment.content_type:
        reject_completion("stored object MIME type does not match declaration")
    metadata = {str(k).lower(): str(v) for k, v in (stat.metadata or {}).items()}
    stored_checksum = metadata.get("x-amz-meta-sha256") or metadata.get("sha256")
    response = blob_store.client.get_object(blob_store.bucket, attachment.id)
    digest = hashlib.sha256()
    streamed_size = 0
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False) as temporary:
            temporary_path = Path(temporary.name)
            while chunk := response.read(1024 * 1024):
                streamed_size += len(chunk)
                digest.update(chunk)
                temporary.write(chunk)
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()
        release = getattr(response, "release_conn", None)
        if callable(release):
            release()
    server_checksum = digest.hexdigest()
    if streamed_size != attachment.size_bytes:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        reject_completion("streamed object size does not match declaration", server_checksum)
    if stored_checksum and stored_checksum != server_checksum:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        reject_completion("object checksum metadata does not match content", server_checksum)
    if body.checksum_sha256 != server_checksum:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        reject_completion("declared checksum does not match object content", server_checksum)
    try:
        if temporary_path is None:
            reject_completion("object could not be staged for scanning", server_checksum)
        scan = blob_store.scanner.scan(temporary_path)
    except Exception as exc:
        reject_completion(f"malware scan failed: {exc}", server_checksum)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    if scan.status is not MalwareScanStatus.CLEAN:
        blob_store.delete(attachment.id)
        return repositories.attachments.update(
            attachment.model_copy(
                update={
                    "storage_uri": None,
                    "checksum_sha256": server_checksum,
                    "status": AttachmentStatus.FAILED,
                    "scan_status": AttachmentScanStatus(scan.status.value),
                    "scan_detail": scan.detail,
                }
            )
        )
    return repositories.attachments.update(
        attachment.model_copy(
            update={
                "storage_uri": f"s3://{blob_store.bucket}/{attachment.id}",
                "checksum_sha256": server_checksum,
                "status": AttachmentStatus.READY,
                "scan_status": AttachmentScanStatus.CLEAN,
                "scan_detail": scan.detail,
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
    cancelled_at = datetime.now(UTC)
    terminal_event = ActivityEvent(
        id=f"event_{run.id}_cancelled",
        thread_id=run.thread_id,
        workspace_id=run.workspace_id,
        run_id=run.id,
        owner_user_id=run.requested_by_user_id,
        actor_user_id=principal.user_id,
        event_type="run.cancelled",
        payload={
            "mission_id": run.mission_id,
            "status": RunStatus.CANCELLED.value.lower(),
        },
        completed_at=cancelled_at,
    )
    if not repositories.runs.cancel(
        run_id, now=cancelled_at, terminal_event=terminal_event
    ):
        raise HTTPException(status_code=409, detail="run is not cancellable")
    cancellation = getattr(runtime, "cancellation", None)
    if cancellation is not None and run.mission_id:
        with suppress(Exception):
            cancellation.request(run.mission_id)
    if run.mission_id:
        try:
            cancel_running_mission(runtime, mission_id=run.mission_id)
        except (KeyError, ValueError):
            pass
    with suppress(Exception):
        _event_sink(runtime, repositories).notifier.publish(
            run.id, terminal_event.sequence
        )
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
            events = await asyncio.to_thread(
                repositories.runs.list_events, run_id, after_sequence=cursor
            )
            for event in events:
                cursor = max(cursor, event.sequence)
                encoded = _sse_event(event, principal)
                if encoded is not None:
                    yield encoded
            run = await asyncio.to_thread(repositories.runs.get, run_id)
            if run is None or run.status in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                return
            if await request.is_disconnected():
                return
            yield ": heartbeat\n\n"
            try:
                await sink.notifier.wait(run_id, cursor, heartbeat_seconds)
            except Exception:
                # Notification is only a latency optimization; durable polling is
                # authoritative and must survive Redis/pubsub outages.
                await asyncio.sleep(heartbeat_seconds)

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
                cursor = max(cursor, event.thread_sequence)
                encoded = _sse_event(event, principal, cursor=event.thread_sequence)
                if encoded is not None:
                    yield encoded
            if await request.is_disconnected():
                return
            yield ": heartbeat\n\n"
            await asyncio.sleep(heartbeat_seconds)

    return _stream_response(generate())


def _artifact_classification(
    artifact_type: object,
    payload: dict[str, Any],
    evidence_ids: list[str],
) -> ArtifactClassification:
    if str(artifact_type).lower() == "evidence":
        return "factual"
    declared = str(payload.get("classification") or "").lower()
    if declared in {"ui", "factual", "derived"}:
        return cast(ArtifactClassification, declared)
    if evidence_ids:
        return "derived"
    raise ValueError("missing explicit classification and evidence provenance")


def _attempt_event(
    run: Run,
    attempt: RunAttempt,
    event_type: str,
    *,
    suffix: str,
    payload: dict[str, Any] | None = None,
    completed_at: datetime | None = None,
) -> ActivityEvent:
    return ActivityEvent(
        id=f"event_{run.id}_{attempt.id}_{suffix}",
        thread_id=run.thread_id,
        workspace_id=run.workspace_id,
        run_id=run.id,
        owner_user_id=run.requested_by_user_id,
        event_type=event_type,
        payload={
            "mission_id": run.mission_id,
            "attempt_id": attempt.id,
            "attempt_number": attempt.attempt_number,
            **(payload or {}),
        },
        completed_at=completed_at,
    )


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
    assistant_message_id: str,
    full_diagnostic: bool = True,
    full_prediction: bool = True,
    full_skeptic: bool = True,
    full_strategy: bool = True,
) -> RunExecutionResult:
    sink = _event_sink(runtime, repositories)
    if not attempt.worker_id:
        raise RuntimeError("submission executor requires a claimed run attempt")
    persisted_before_start = repositories.runs.get(run.id)
    cancellation = getattr(runtime, "cancellation", None)
    cancelled_before_start = (
        persisted_before_start is not None
        and persisted_before_start.status is RunStatus.CANCELLED
    ) or (
        bool(run.mission_id)
        and cancellation is not None
        and cancellation.is_requested(run.mission_id)
    )
    if cancelled_before_start:
        completed_at = datetime.now(UTC)

        return RunExecutionResult(
            status=RunStatus.CANCELLED,
            error_code="CANCELLED",
            terminal_event=_attempt_event(
                run,
                attempt,
                "run.cancelled",
                suffix="terminal",
                completed_at=completed_at,
            ),
        )
    running = repositories.runs.compare_and_set_status(
        run.id,
        {RunStatus.QUEUED},
        RunStatus.RUNNING,
    )
    if running is None:
        persisted = repositories.runs.get(run.id)
        if persisted is not None and persisted.status is RunStatus.CANCELLED:
            return RunExecutionResult(
                status=RunStatus.CANCELLED,
                error_code="CANCELLED",
                terminal_event=_attempt_event(
                    run,
                    attempt,
                    "run.cancelled",
                    suffix="terminal",
                    completed_at=datetime.now(UTC),
                ),
            )
        raise RuntimeError(f"run {run.id} could not enter RUNNING state")
    sink.emit(
        running,
        "run.started",
        id=f"event_{run.id}_{attempt.id}_started",
        payload={
            "mission_id": run.mission_id,
            "attempt_id": attempt.id,
            "attempt_number": attempt.attempt_number,
        },
        started_at=running.started_at,
    )

    def persist_incremental_event(event: dict[str, Any]) -> None:
        sink.ingest_mission_events(
            running,
            [event],
            attempt_id=attempt.id,
            exclude_event_types={
                "run.started",
                "run.completed",
                "run.failed",
                "run.cancelled",
            },
        )

    with observe_mission_events(persist_incremental_event):
        await run_mission_job(
            runtime,
            mission_id=run.mission_id or "",
            query=query,
            timezone=timezone,
            as_of=as_of,
            session_id=run.thread_id,
            request_id=request_id,
            full_diagnostic=full_diagnostic,
            full_prediction=full_prediction,
            full_skeptic=full_skeptic,
            full_strategy=full_strategy,
            execution_mode=execution_mode,
        )
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
            attempt_id=attempt.id,
            exclude_event_types={
                "run.started",
                "run.completed",
                "run.failed",
                "run.cancelled",
            },
        )
    mission_status = raw.get("status")
    final_status = {
        "failed": RunStatus.FAILED,
        "cancelled": RunStatus.CANCELLED,
    }.get(str(mission_status), RunStatus.COMPLETED)
    persisted_run = repositories.runs.get(run.id)
    if persisted_run is not None and persisted_run.status is RunStatus.CANCELLED:
        final_status = RunStatus.CANCELLED
    completed_at = datetime.now(UTC)
    final_response = raw.get("final_response")
    if isinstance(final_response, str) and final_response.strip():
        placeholder = repositories.messages.get(assistant_message_id)
        if placeholder is None:
            raise RuntimeError("assistant placeholder is missing")
        final_message = repositories.messages.update(
            placeholder.model_copy(
                update={
                    "parts": [
                        MessagePart(type=MessagePartType.TEXT, content=final_response)
                    ],
                    "updated_at": datetime.now(UTC),
                }
            )
        )
        sink.emit(
            running,
            "answer.completed",
            id=f"event_{run.id}_{attempt.id}_answer_completed",
            payload={"message_id": final_message.id, "mission_id": run.mission_id},
        )
        summary_thread = repositories.threads.get(run.thread_id)
        if summary_thread is not None:
            ThreadSummaryService(repositories).create(
                summary_thread,
                repositories.messages.list_for_thread(run.thread_id, limit=500),
                mode="deterministic",
            )
    else:
        placeholder = repositories.messages.get(assistant_message_id)
        if placeholder is not None:
            repositories.messages.update(
                placeholder.model_copy(
                    update={
                        "parts": [
                            MessagePart(
                                type=MessagePartType.WARNING,
                                content=(
                                    "Run cancelled."
                                    if final_status is RunStatus.CANCELLED
                                    else "Run failed without a response."
                                ),
                                metadata={"status": final_status.value.lower()},
                            )
                        ],
                        "updated_at": datetime.now(UTC),
                    }
                )
            )
    artifact_groups = raw.get("artifacts")
    if isinstance(artifact_groups, dict):
        for artifact_type, items in artifact_groups.items():
            for artifact_position, payload in enumerate(
                items if isinstance(items, list) else []
            ):
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
                try:
                    classification = _artifact_classification(
                        artifact_type, payload, evidence_ids
                    )
                except ValueError as exc:
                    sink.emit(
                        running,
                        "artifact.rejected",
                        payload={
                            "artifact_type": str(artifact_type),
                            "reason": str(exc),
                        },
                    )
                    continue
                try:
                    artifact = repositories.artifacts.put(
                        Artifact(
                            id=str(
                                payload.get("artifact_id")
                                or payload.get("id")
                                or (
                                    "artifact_"
                                    + hashlib.sha256(
                                        (
                                            f"{run.id}:{attempt.id}:{artifact_type}:"
                                            f"{artifact_position}:"
                                            + json.dumps(
                                                payload,
                                                sort_keys=True,
                                                separators=(",", ":"),
                                                default=str,
                                            )
                                        ).encode()
                                    ).hexdigest()[:32]
                                )
                            ),
                            workspace_id=run.workspace_id,
                            artifact_type=str(artifact_type),
                            payload=payload,
                            classification=classification,
                            evidence_ids=list(dict.fromkeys(evidence_ids)),
                            provenance=ArtifactProvenance(
                                evidence_ids=list(dict.fromkeys(evidence_ids)),
                                calculation_version=(
                                    str(
                                        payload.get("calculation_version")
                                        or _execution_setting(
                                            runtime, "workflow_version", "unknown"
                                        )
                                    )
                                    if classification == "derived"
                                    else None
                                ),
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
                except ValueError as exc:
                    sink.emit(
                        running,
                        "artifact.rejected",
                        payload={
                            "artifact_type": str(artifact_type),
                            "reason": str(exc),
                        },
                    )
                    continue
                sink.emit(
                    running,
                    "artifact.created",
                    id=f"event_{run.id}_{attempt.id}_artifact_{artifact.id}",
                    payload={
                        "artifact_id": artifact.id,
                        "artifact_type": artifact.artifact_type,
                    },
                )
    return RunExecutionResult(
        status=final_status,
        error_code=str(raw.get("error_code") or "") or None,
        error_message=str(raw.get("error_message") or "") or None,
        retryable=(
            final_status is RunStatus.FAILED
            and str(raw.get("error_code") or "").upper()
            in {
                "ASYNC_EXECUTION_FAILED",
                "LLM_UNAVAILABLE",
                "LLM_CLASSIFICATION_UNAVAILABLE",
                "MCP_ERROR",
                "MCP_UNAVAILABLE",
                "MISSION_TIMEOUT",
                "SERVICE_UNAVAILABLE",
                "TIMEOUT",
            }
        ),
        terminal_event=_attempt_event(
            running,
            attempt,
            {
                RunStatus.COMPLETED: "run.completed",
                RunStatus.CANCELLED: "run.cancelled",
            }.get(final_status, "run.failed"),
            suffix="terminal",
            completed_at=completed_at,
        ),
    )


class SubmissionRunExecutor:
    """Rehydrate persisted submission inputs and resume one claimed attempt."""

    def __init__(
        self,
        runtime: SwarmRuntime,
        repositories: ConversationRepositories,
    ) -> None:
        self.runtime = runtime
        self.repositories = repositories

    async def __call__(
        self, run: Run, attempt: RunAttempt
    ) -> RunExecutionResult:
        submission = run.metadata.get("submission")
        if not isinstance(submission, dict):
            submission = {}
        raw = getattr(self.runtime.store, "get_raw", lambda _mission_id: None)(
            run.mission_id
        )
        raw_input = raw.get("input") if isinstance(raw, dict) else {}
        raw_input = raw_input if isinstance(raw_input, dict) else {}
        query = str(submission.get("query") or raw_input.get("query") or "").strip()
        if not query:
            raise RuntimeError(f"run {run.id} has no persisted submission query")
        assistant_message_id = str(submission.get("assistant_message_id") or "")
        if not assistant_message_id:
            assistant_message_id = next(
                (
                    message.id
                    for message in self.repositories.messages.list_for_thread(
                        run.thread_id, limit=500
                    )
                    if message.run_id == run.id and message.role is MessageRole.ASSISTANT
                ),
                "",
            )
        if not assistant_message_id:
            raise RuntimeError(f"run {run.id} has no assistant placeholder")
        return await _execute_submission(
            self.runtime,
            self.repositories,
            run=run,
            attempt=attempt,
            query=query,
            timezone=str(submission.get("timezone") or "Asia/Kolkata"),
            as_of=(
                str(submission["as_of"])
                if submission.get("as_of") is not None
                else None
            ),
            request_id=str(submission.get("request_id") or run.id),
            execution_mode=str(submission.get("execution_mode") or "production"),
            assistant_message_id=assistant_message_id,
            full_diagnostic=bool(submission.get("full_diagnostic", True)),
            full_prediction=bool(submission.get("full_prediction", True)),
            full_skeptic=bool(submission.get("full_skeptic", True)),
            full_strategy=bool(submission.get("full_strategy", True)),
        )


def build_submission_executor(runtime: SwarmRuntime) -> SubmissionRunExecutor:
    repositories = _repositories(runtime)
    return SubmissionRunExecutor(runtime, repositories)


def _submission_queue(
    runtime: SwarmRuntime, repositories: ConversationRepositories
) -> RunWorkQueue:
    queue = getattr(runtime, "run_queue", None)
    if queue is not None:
        return queue
    worker_id = _execution_setting(runtime, "run_worker_id", "") or (
        f"inprocess-{uuid4().hex[:12]}"
    )
    queue = InProcessRunQueue(
        RunRecoveryWorker(
            repositories.runs,
            SubmissionRunExecutor(runtime, repositories),
            worker_id=worker_id,
            lease_s=_execution_setting(runtime, "run_lease_s", 60.0),
            heartbeat_s=_execution_setting(runtime, "run_heartbeat_s", 15.0),
            retry_delay_s=_execution_setting(runtime, "run_retry_delay_s", 5.0),
            retry_jitter_s=_execution_setting(runtime, "run_retry_jitter_s", 1.0),
        )
    )
    try:
        runtime.run_queue = queue
    except (AttributeError, TypeError):
        pass
    return queue


@router.post(
    "/threads/{thread_id}/messages",
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_message(
    thread_id: str,
    body: SubmitMessageRequest,
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
    if body.parent_message_id is not None:
        parent = repositories.messages.get(
            body.parent_message_id, principal.workspace_id
        )
        if parent is None or parent.thread_id != thread.id:
            raise HTTPException(
                status_code=422,
                detail="parent message must belong to this thread and workspace",
            )
    query = "\n".join(
        str(part.content).strip()
        for part in body.parts
        if part.type.value == "TEXT" and isinstance(part.content, str) and part.content.strip()
    )
    if not query:
        raise HTTPException(status_code=400, detail="at least one non-empty TEXT part is required")
    attachments: list[Attachment] = []
    for attachment_id in dict.fromkeys(body.attachment_ids):
        attachment = _owned_attachment(repositories, principal, attachment_id)
        if attachment.thread_id != thread.id:
            raise HTTPException(status_code=422, detail="attachment belongs to another thread")
        if (
            attachment.status is not AttachmentStatus.READY
            or attachment.scan_status is not AttachmentScanStatus.CLEAN
        ):
            raise HTTPException(status_code=409, detail="attachment is not ready")
        if attachment.message_id is not None:
            raise HTTPException(status_code=409, detail="attachment is already associated")
        attachments.append(attachment)

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
    try:
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
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Unable to persist the mission before submission",
        ) from exc
    try:
        with repositories.transaction() as writes:
            run = writes.runs.create(run)
            writes.runs.add_attempt(
                RunAttempt(
                    run_id=run.id,
                    attempt_number=1,
                    status=RunAttemptStatus.RETRYABLE,
                )
            )
            message = writes.messages.create(
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
            if not writes.attachments.associate_many(
                [attachment.id for attachment in attachments],
                message.id,
                thread_id=thread.id,
                workspace_id=thread.workspace_id,
                owner_user_id=principal.user_id,
            ):
                raise HTTPException(
                    status_code=409,
                    detail="one or more attachments were concurrently associated",
                )
            assistant_message = writes.messages.create(
                Message(
                    thread_id=thread.id,
                    workspace_id=thread.workspace_id,
                    role=MessageRole.ASSISTANT,
                    run_id=run.id,
                    parent_message_id=message.id,
                    created_at=message.created_at + timedelta(microseconds=1),
                    updated_at=message.created_at + timedelta(microseconds=1),
                    parts=[
                        MessagePart(
                            type=MessagePartType.AGENT_STATUS,
                            content="Working…",
                            metadata={"status": "pending"},
                        )
                    ],
                )
            )
            run = writes.runs.update(
                run.model_copy(
                    update={
                        "metadata": {
                            **run.metadata,
                            "context_bundle": bundle_data,
                            "submission": {
                                "query": query,
                                "timezone": str(
                                    body.scope.get("timezone") or "Asia/Kolkata"
                                ),
                                "as_of": body.scope.get("as_of")
                                or body.scope.get("asOf"),
                                "request_id": request_id,
                                "execution_mode": body.execution_mode,
                                "assistant_message_id": assistant_message.id,
                            },
                        }
                    }
                )
            )
            writes.memories.record_usage(
                run.id,
                context_bundle.memories,
                {
                    "thread_id": thread.id,
                    "mission_id": mission_id,
                    "builder": "phase6-v1",
                },
            )
            title = thread.title
            if not title or title.strip().lower() == "untitled conversation":
                title = " ".join(query.split())[:80]
            writes.threads.update(
                thread.model_copy(
                    update={"title": title, "updated_at": datetime.now(UTC)}
                )
            )
            writes.runs.add_outbox(run.id)
    except HTTPException:  # noqa: TRY203 - transaction context must observe the error
        raise
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
    _event_sink(runtime, repositories).emit(
        run,
        "run.queued",
        payload={"message_id": message.id, "mission_id": mission_id},
        actor_user_id=principal.user_id,
    )
    try:
        await _submission_queue(runtime, repositories).enqueue(run.id)
    except Exception:  # noqa: S110 - durable outbox is the recovery record
        # The committed outbox row is intentionally left pending. Recovery
        # dispatchers can deterministically retry this idempotent run enqueue.
        pass
    else:
        repositories.runs.mark_outbox_published(run.id)
    return {"message_id": message.id, "run_id": run.id, "mission_id": mission_id}
