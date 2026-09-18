from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from seleric_swarm.api import conversations as conversations_api
from seleric_swarm.api.security import ApiSecurityMiddleware
from seleric_swarm.conversations.blobs import (
    LocalBlobStore,
    MalwareScanResult,
    MalwareScanStatus,
    MinioBlobStore,
)
from seleric_swarm.conversations.contracts import (
    Attachment,
    AttachmentScanStatus,
    AttachmentStatus,
    Thread,
)
from seleric_swarm.conversations.memory import build_in_memory_repositories
from seleric_swarm.persistence.memory import InMemoryMissionStore


class _CleanScanner:
    def scan(self, path: Path) -> MalwareScanResult:
        assert path.is_file()
        return MalwareScanResult(MalwareScanStatus.CLEAN)


class _FakeMinio:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, str, dict[str, str]]] = {}

    def get_object(self, bucket_name: str, object_name: str) -> BytesIO:
        return BytesIO(self.objects[(bucket_name, object_name)][0])

    def stat_object(self, bucket_name: str, object_name: str) -> object:
        body, content_type, metadata = self.objects[(bucket_name, object_name)]
        return SimpleNamespace(size=len(body), content_type=content_type, metadata=metadata)

    def remove_object(self, bucket_name: str, object_name: str) -> None:
        self.objects.pop((bucket_name, object_name), None)

    def presigned_put_object(self, bucket_name: str, object_name: str, expires) -> str:
        return f"https://minio.invalid/{bucket_name}/{object_name}?put"

    def presigned_get_object(self, bucket_name: str, object_name: str, expires) -> str:
        return f"https://minio.invalid/{bucket_name}/{object_name}?get"


def _client(blob_store) -> tuple[TestClient, object, Thread]:
    repositories = build_in_memory_repositories()
    thread = repositories.threads.create(Thread(workspace_id="w", owner_user_id="u"))
    runtime = SimpleNamespace(
        conversations=repositories,
        blob_store=blob_store,
        store=InMemoryMissionStore(),
    )
    app = FastAPI()
    app.state.runtime_provider = lambda: runtime
    app.include_router(conversations_api.router)
    app.add_middleware(
        ApiSecurityMiddleware,
        api_key="secret",
        rate_limit_enabled=False,
        default_workspace_id="w",
        default_user_id="u",
        trust_identity_headers=True,
    )
    return TestClient(app), repositories, thread


def _headers() -> dict[str, str]:
    return {
        "X-API-Key": "secret",
        "X-Workspace-ID": "w",
        "X-User-ID": "u",
    }


def _initiate(client: TestClient, thread: Thread, *, size: int = 5) -> str:
    response = client.post(
        f"/v1/threads/{thread.id}/attachments",
        json={"filename": "note.txt", "content_type": "text/plain", "size_bytes": size},
        headers=_headers(),
    )
    assert response.status_code == 201
    return str(response.json()["attachment"]["id"])


def test_minio_initiation_uses_shared_mime_and_size_validation() -> None:
    store = MinioBlobStore(
        _FakeMinio(),
        "attachments",
        max_size_bytes=5,
        allowed_mime_types={"text/plain"},
        scanner=_CleanScanner(),
    )
    client, _, thread = _client(store)

    bad_mime = client.post(
        f"/v1/threads/{thread.id}/attachments",
        json={"filename": "payload.zip", "content_type": "application/zip", "size_bytes": 1},
        headers=_headers(),
    )
    too_large = client.post(
        f"/v1/threads/{thread.id}/attachments",
        json={"filename": "note.txt", "content_type": "text/plain", "size_bytes": 6},
        headers=_headers(),
    )

    assert bad_mime.status_code == 415
    assert too_large.status_code == 413


@pytest.mark.parametrize(
    ("stored_type", "declared_digest", "expected_detail"),
    [
        ("application/pdf", hashlib.sha256(b"hello").hexdigest(), "MIME"),
        ("text/plain", hashlib.sha256(b"other").hexdigest(), "declared checksum"),
    ],
)
def test_minio_completion_removes_tampered_objects(
    stored_type: str,
    declared_digest: str,
    expected_detail: str,
) -> None:
    fake = _FakeMinio()
    store = MinioBlobStore(
        fake,
        "attachments",
        max_size_bytes=10,
        allowed_mime_types={"text/plain", "application/pdf"},
        scanner=_CleanScanner(),
    )
    client, repositories, thread = _client(store)
    attachment_id = _initiate(client, thread)
    fake.objects[("attachments", attachment_id)] = (b"hello", stored_type, {})

    response = client.post(
        f"/v1/attachments/{attachment_id}/complete",
        json={"checksum_sha256": declared_digest},
        headers=_headers(),
    )

    assert response.status_code == 409
    assert expected_detail.lower() in response.json()["detail"].lower()
    assert ("attachments", attachment_id) not in fake.objects
    assert repositories.attachments.get(attachment_id).status is AttachmentStatus.FAILED


def test_minio_completion_removes_wrong_sized_object() -> None:
    fake = _FakeMinio()
    store = MinioBlobStore(
        fake,
        "attachments",
        max_size_bytes=10,
        allowed_mime_types={"text/plain"},
        scanner=_CleanScanner(),
    )
    client, repositories, thread = _client(store)
    attachment_id = _initiate(client, thread)
    fake.objects[("attachments", attachment_id)] = (b"too-long", "text/plain", {})

    response = client.post(
        f"/v1/attachments/{attachment_id}/complete",
        json={"checksum_sha256": hashlib.sha256(b"too-long").hexdigest()},
        headers=_headers(),
    )

    assert response.status_code == 409
    assert ("attachments", attachment_id) not in fake.objects
    assert repositories.attachments.get(attachment_id).status is AttachmentStatus.FAILED


def test_minio_completion_uses_server_digest_and_requires_pending() -> None:
    fake = _FakeMinio()
    store = MinioBlobStore(
        fake,
        "attachments",
        max_size_bytes=10,
        allowed_mime_types={"text/plain"},
        scanner=_CleanScanner(),
    )
    client, _, thread = _client(store)
    attachment_id = _initiate(client, thread)
    digest = hashlib.sha256(b"hello").hexdigest()
    fake.objects[("attachments", attachment_id)] = (b"hello", "text/plain", {})

    completed = client.post(
        f"/v1/attachments/{attachment_id}/complete",
        json={"checksum_sha256": digest},
        headers=_headers(),
    )
    repeated = client.post(
        f"/v1/attachments/{attachment_id}/complete",
        json={"checksum_sha256": digest},
        headers=_headers(),
    )

    assert completed.status_code == 200
    assert completed.json()["checksum_sha256"] == digest
    assert completed.json()["scan_status"] == "CLEAN"
    assert repeated.status_code == 409


def test_minio_completion_fails_closed_when_scanner_is_unavailable() -> None:
    fake = _FakeMinio()
    store = MinioBlobStore(
        fake,
        "attachments",
        max_size_bytes=10,
        allowed_mime_types={"text/plain"},
    )
    client, _, thread = _client(store)
    attachment_id = _initiate(client, thread)
    digest = hashlib.sha256(b"hello").hexdigest()
    fake.objects[("attachments", attachment_id)] = (b"hello", "text/plain", {})

    completed = client.post(
        f"/v1/attachments/{attachment_id}/complete",
        json={"checksum_sha256": digest},
        headers=_headers(),
    )

    assert completed.status_code == 200
    assert completed.json()["status"] == "FAILED"
    assert completed.json()["scan_status"] == "UNAVAILABLE"
    assert ("attachments", attachment_id) not in fake.objects


def test_local_api_fails_closed_when_scanner_is_unavailable(tmp_path: Path) -> None:
    client, _, thread = _client(
        LocalBlobStore(
            tmp_path,
            max_size_bytes=10,
            allowed_mime_types={"text/plain"},
        )
    )
    attachment_id = _initiate(client, thread)

    uploaded = client.put(
        f"/v1/attachments/{attachment_id}/content",
        content=b"hello",
        headers={**_headers(), "Content-Type": "text/plain"},
    )

    assert uploaded.status_code == 200
    assert uploaded.json()["status"] == "FAILED"
    assert uploaded.json()["scan_status"] == "UNAVAILABLE"


def test_attachment_association_is_all_or_none_and_race_safe() -> None:
    repository = build_in_memory_repositories().attachments
    ready = Attachment(
        id="ready",
        thread_id="thread",
        workspace_id="w",
        owner_user_id="u",
        filename="note.txt",
        content_type="text/plain",
        size_bytes=5,
        storage_uri="file:///note.txt",
        checksum_sha256=hashlib.sha256(b"hello").hexdigest(),
        status=AttachmentStatus.READY,
        scan_status=AttachmentScanStatus.CLEAN,
    )
    repository.create(ready)

    assert not repository.associate_many(
        ["ready", "missing"],
        "message-invalid",
        thread_id="thread",
        workspace_id="w",
        owner_user_id="u",
    )
    assert repository.get("ready").message_id is None

    def associate(message_id: str) -> bool:
        return repository.associate_many(
            ["ready"],
            message_id,
            thread_id="thread",
            workspace_id="w",
            owner_user_id="u",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(associate, ["message-one", "message-two"]))

    assert sorted(outcomes) == [False, True]
    assert repository.get("ready").message_id in {"message-one", "message-two"}


def test_api_rolls_back_run_when_attachment_association_loses_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, repositories, thread = _client(
        LocalBlobStore(
            tmp_path,
            max_size_bytes=10,
            allowed_mime_types={"text/plain"},
            scanner=_CleanScanner(),
        )
    )
    attachment = repositories.attachments.create(
        Attachment(
            thread_id=thread.id,
            workspace_id="w",
            owner_user_id="u",
            filename="note.txt",
            content_type="text/plain",
            size_bytes=5,
            storage_uri="file:///note.txt",
            checksum_sha256=hashlib.sha256(b"hello").hexdigest(),
            status=AttachmentStatus.READY,
            scan_status=AttachmentScanStatus.CLEAN,
        )
    )
    monkeypatch.setattr(
        repositories.attachments,
        "associate_many",
        lambda *args, **kwargs: False,
    )

    response = client.post(
        f"/v1/threads/{thread.id}/messages",
        json={
            "parts": [{"type": "TEXT", "content": "Use the attachment"}],
            "attachment_ids": [attachment.id],
        },
        headers=_headers(),
    )

    assert response.status_code == 409
    assert repositories.runs.list_for_owner("w", "u") == []
    assert repositories.runs.list_recoverable() == []


def test_artifact_ingestion_rejects_missing_provenance_instead_of_assuming_ui() -> None:
    with pytest.raises(ValueError, match="classification and evidence provenance"):
        conversations_api._artifact_classification("report", {"value": 42}, [])
    assert (
        conversations_api._artifact_classification("report", {"value": 42}, ["evidence-1"])
        == "derived"
    )
    assert (
        conversations_api._artifact_classification("layout", {"classification": "ui"}, []) == "ui"
    )
