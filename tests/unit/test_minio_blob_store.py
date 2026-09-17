from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from seleric_swarm.conversations.blobs import (
    BlobValidationError,
    MalwareScanResult,
    MalwareScanStatus,
    MinioBlobStore,
)


async def _chunks(*values: bytes):
    for value in values:
        yield value


class _CleanScanner:
    def scan(self, path: Path) -> MalwareScanResult:
        assert path.read_bytes()
        return MalwareScanResult(MalwareScanStatus.CLEAN)


class _FakeMinio:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, str, dict[str, str]]] = {}
        self.bad_checksum_metadata = False

    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data,
        length: int,
        content_type: str,
        metadata: dict[str, str],
    ) -> object:
        body = data.read()
        assert len(body) == length
        self.objects[(bucket_name, object_name)] = (body, content_type, metadata)
        return object()

    def get_object(self, bucket_name: str, object_name: str) -> BytesIO:
        return BytesIO(self.objects[(bucket_name, object_name)][0])

    def stat_object(self, bucket_name: str, object_name: str) -> object:
        body, content_type, metadata = self.objects[(bucket_name, object_name)]
        if self.bad_checksum_metadata:
            metadata = {"sha256": "bad"}
        return SimpleNamespace(size=len(body), content_type=content_type, metadata=metadata)

    def remove_object(self, bucket_name: str, object_name: str) -> None:
        self.objects.pop((bucket_name, object_name), None)

    def presigned_put_object(self, bucket_name: str, object_name: str, expires) -> str:
        return f"https://minio.invalid/{bucket_name}/{object_name}?put"

    def presigned_get_object(self, bucket_name: str, object_name: str, expires) -> str:
        return f"https://minio.invalid/{bucket_name}/{object_name}?get"


async def test_minio_put_get_delete_verifies_server_metadata() -> None:
    client = _FakeMinio()
    store = MinioBlobStore(
        client,
        "attachments",
        max_size_bytes=16,
        allowed_mime_types={"text/plain"},
        scanner=_CleanScanner(),
    )

    result = await store.put(
        "thread/note.txt",
        _chunks(b"hel", b"lo"),
        content_type="text/plain; charset=utf-8",
        expected_size=5,
    )

    assert result.ready
    assert result.checksum_sha256 == hashlib.sha256(b"hello").hexdigest()
    assert store.get("thread/note.txt").read() == b"hello"
    store.delete("thread/note.txt")
    assert client.objects == {}


async def test_minio_default_scanner_quarantines_without_upload() -> None:
    client = _FakeMinio()
    store = MinioBlobStore(
        client,
        "attachments",
        max_size_bytes=16,
        allowed_mime_types={"text/plain"},
    )

    result = await store.put("note.txt", _chunks(b"hello"), content_type="text/plain")

    assert result.scan.status is MalwareScanStatus.UNAVAILABLE
    assert not result.ready
    assert result.uri.startswith("quarantine://")
    assert client.objects == {}


async def test_minio_removes_object_when_remote_metadata_cannot_be_verified() -> None:
    client = _FakeMinio()
    store = MinioBlobStore(
        client,
        "attachments",
        max_size_bytes=16,
        allowed_mime_types={"text/plain"},
        scanner=_CleanScanner(),
    )
    client.bad_checksum_metadata = True

    with pytest.raises(BlobValidationError, match="checksum"):
        await store.put("note.txt", _chunks(b"hello"), content_type="text/plain")

    assert client.objects == {}


async def test_minio_get_rejects_unverified_checksum_metadata() -> None:
    client = _FakeMinio()
    store = MinioBlobStore(
        client,
        "attachments",
        max_size_bytes=16,
        allowed_mime_types={"text/plain"},
        scanner=_CleanScanner(),
    )
    await store.put("note.txt", _chunks(b"hello"), content_type="text/plain")
    client.bad_checksum_metadata = True

    with pytest.raises(BlobValidationError, match="checksum metadata"):
        store.get("note.txt")
