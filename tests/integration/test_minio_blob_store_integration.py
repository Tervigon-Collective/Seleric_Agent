from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from minio import Minio

from seleric_swarm.conversations.blobs import (
    MalwareScanResult,
    MalwareScanStatus,
    MinioBlobStore,
)


async def _chunks(value: bytes):
    yield value


class _CleanScanner:
    def scan(self, path: Path) -> MalwareScanResult:
        assert path.is_file()
        return MalwareScanResult(MalwareScanStatus.CLEAN)


@pytest.mark.integration
async def test_minio_blob_round_trip_when_service_is_available() -> None:
    endpoint = os.getenv("MINIO_TEST_ENDPOINT", "").strip()
    if not endpoint:
        pytest.skip("MINIO_TEST_ENDPOINT is not configured")
    client = Minio(
        endpoint,
        access_key=os.getenv("MINIO_TEST_ACCESS_KEY", "minioadmin"),
        secret_key=os.getenv("MINIO_TEST_SECRET_KEY", "minioadmin"),
        secure=os.getenv("MINIO_TEST_SECURE", "false").lower() == "true",
    )
    bucket = f"seleric-test-{uuid4().hex}"
    try:
        client.make_bucket(bucket)
    except Exception as exc:
        pytest.skip(f"MinIO unavailable: {exc}")

    store = MinioBlobStore(
        client,
        bucket,
        max_size_bytes=1024,
        allowed_mime_types={"text/plain"},
        scanner=_CleanScanner(),
    )
    key = "integration/note.txt"
    try:
        result = await store.put(
            key,
            _chunks(b"minio integration"),
            content_type="text/plain",
            expected_size=17,
        )
        response = store.get(key)
        try:
            assert response.read() == b"minio integration"
        finally:
            response.close()
            release_conn = getattr(response, "release_conn", None)
            if callable(release_conn):
                release_conn()
        assert result.ready
        store.delete(key)
    finally:
        client.remove_bucket(bucket)
