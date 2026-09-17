from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from seleric_swarm.api import conversations as conversations_api
from seleric_swarm.api.security import ApiSecurityMiddleware
from seleric_swarm.conversations.blobs import BlobValidationError, LocalBlobStore
from seleric_swarm.conversations.contracts import (
    Artifact,
    ArtifactProvenance,
    MessagePart,
    Thread,
)
from seleric_swarm.conversations.memory import build_in_memory_repositories


async def _chunks(*values: bytes):
    for value in values:
        yield value


async def test_local_blob_hash_size_mime_and_path(tmp_path: Path):
    store = LocalBlobStore(
        tmp_path, max_size_bytes=5, allowed_mime_types={"text/plain"}
    )
    result = await store.put(
        "safe/blob", _chunks(b"he", b"llo"), content_type="text/plain", expected_size=5
    )
    assert result.checksum_sha256 == hashlib.sha256(b"hello").hexdigest()
    assert store.open("safe/blob").read() == b"hello"

    with pytest.raises(BlobValidationError, match="maximum"):
        await store.put("large", _chunks(b"123456"), content_type="text/plain")
    with pytest.raises(BlobValidationError, match="MIME"):
        await store.put("bad", _chunks(b"x"), content_type="application/zip")
    with pytest.raises(BlobValidationError, match="invalid"):
        store.open("../escape")


def test_message_parts_are_typed():
    assert MessagePart(type="TABLE", content=[{"a": 1}]).type.value == "TABLE"
    with pytest.raises(ValueError, match="TOOL_CALL"):
        MessagePart(type="TOOL_CALL", content={})
    with pytest.raises(ValueError, match="object"):
        MessagePart(type="CHART", content="unsafe")


def test_provenance_round_trip_and_boundary_rejection():
    repositories = build_in_memory_repositories()
    artifact = Artifact(
        workspace_id="w",
        artifact_type="comparison",
        classification="derived",
        payload={"value": 2},
        evidence_ids=["EV-1"],
        provenance=ArtifactProvenance(
            evidence_ids=["EV-1"], calculation_version="1"
        ),
    )
    persisted = repositories.artifacts.put(artifact)
    assert repositories.artifacts.get(persisted.id) == persisted
    with pytest.raises(ValueError, match="evidence IDs"):
        repositories.artifacts.put(
            Artifact(
                workspace_id="w",
                artifact_type="fact",
                classification="factual",
                payload={},
            )
        )
    repositories.artifacts.put(
        Artifact(workspace_id="w", artifact_type="layout", payload={})
    )


def _client(tmp_path: Path):
    repositories = build_in_memory_repositories()
    thread = repositories.threads.create(Thread(workspace_id="w", owner_user_id="u"))
    runtime = SimpleNamespace(
        conversations=repositories,
        blob_store=LocalBlobStore(
            tmp_path, max_size_bytes=100, allowed_mime_types={"text/plain"}
        ),
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


def _headers(user: str = "u"):
    return {
        "X-API-Key": "secret",
        "X-Workspace-ID": "w",
        "X-User-ID": user,
    }


def test_api_local_upload_download_and_ownership(tmp_path: Path):
    client, _, thread = _client(tmp_path)
    initiated = client.post(
        f"/v1/threads/{thread.id}/attachments",
        json={"filename": "note.txt", "content_type": "text/plain", "size_bytes": 5},
        headers=_headers(),
    )
    assert initiated.status_code == 201
    payload = initiated.json()
    attachment_id = payload["attachment"]["id"]
    uploaded = client.put(
        payload["upload"]["url"],
        content=b"hello",
        headers={**_headers(), "Content-Type": "text/plain"},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["status"] == "READY"
    assert uploaded.json()["checksum_sha256"] == hashlib.sha256(b"hello").hexdigest()
    assert client.get(
        f"/v1/attachments/{attachment_id}/download", headers=_headers()
    ).content == b"hello"
    assert client.get(
        f"/v1/attachments/{attachment_id}", headers=_headers("other")
    ).status_code == 404
