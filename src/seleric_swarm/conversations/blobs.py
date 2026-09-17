"""Secure streaming blob storage used by conversation attachments."""

from __future__ import annotations

import hashlib
import mimetypes
from collections.abc import AsyncIterable, Iterator
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol


class BlobValidationError(ValueError):
    pass


class MalwareScanStatus(StrEnum):
    CLEAN = "CLEAN"
    QUARANTINED = "QUARANTINED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class MalwareScanResult:
    status: MalwareScanStatus
    detail: str = ""


class MalwareScanner(Protocol):
    def scan(self, path: Path) -> MalwareScanResult: ...


class NoOpMalwareScanner:
    """Explicit default policy for deployments without a scanner."""

    def scan(self, path: Path) -> MalwareScanResult:
        return MalwareScanResult(MalwareScanStatus.CLEAN, "scanner not configured")


@dataclass(frozen=True)
class BlobWriteResult:
    uri: str
    size_bytes: int
    checksum_sha256: str
    scan: MalwareScanResult


class BlobStore(Protocol):
    async def put(
        self,
        key: str,
        chunks: AsyncIterable[bytes],
        *,
        content_type: str,
        expected_size: int | None = None,
    ) -> BlobWriteResult: ...

    def open(self, key: str) -> BinaryIO: ...
    def delete(self, key: str) -> None: ...
    def presign_upload(self, key: str, *, content_type: str, expires_s: int = 900) -> str: ...
    def presign_download(self, key: str, *, expires_s: int = 900) -> str: ...


class MinioClient(Protocol):
    def get_object(self, bucket_name: str, object_name: str) -> BinaryIO: ...
    def remove_object(self, bucket_name: str, object_name: str) -> None: ...
    def presigned_put_object(
        self, bucket_name: str, object_name: str, expires: timedelta
    ) -> str: ...
    def presigned_get_object(
        self, bucket_name: str, object_name: str, expires: timedelta
    ) -> str: ...


def safe_blob_key(key: str) -> str:
    normalized = key.replace("\\", "/").strip("/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts or any(p in {"", "."} for p in path.parts):
        raise BlobValidationError("invalid blob key")
    return str(path)


class LocalBlobStore:
    def __init__(
        self,
        root: str | Path,
        *,
        max_size_bytes: int = 25 * 1024 * 1024,
        allowed_mime_types: set[str] | None = None,
        scanner: MalwareScanner | None = None,
        public_base_url: str = "/v1/attachments",
    ) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_size_bytes = max_size_bytes
        self.allowed_mime_types = allowed_mime_types or {
            "application/json",
            "application/pdf",
            "text/csv",
            "text/plain",
            "image/jpeg",
            "image/png",
            "image/webp",
        }
        self.scanner = scanner or NoOpMalwareScanner()
        self.public_base_url = public_base_url.rstrip("/")

    def _path(self, key: str) -> Path:
        candidate = (self.root / safe_blob_key(key)).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise BlobValidationError("blob path escapes storage root")
        return candidate

    def validate_metadata(self, filename: str, content_type: str, size_bytes: int | None) -> None:
        clean_type = content_type.split(";", 1)[0].strip().lower()
        if clean_type not in self.allowed_mime_types:
            raise BlobValidationError(f"MIME type is not allowed: {clean_type}")
        if size_bytes is not None and (size_bytes < 0 or size_bytes > self.max_size_bytes):
            raise BlobValidationError("attachment exceeds configured maximum size")
        guessed, _ = mimetypes.guess_type(filename)
        if guessed and clean_type.startswith(("image/", "text/")) and guessed != clean_type:
            raise BlobValidationError("filename extension does not match MIME type")

    async def put(
        self,
        key: str,
        chunks: AsyncIterable[bytes],
        *,
        content_type: str,
        expected_size: int | None = None,
    ) -> BlobWriteResult:
        self.validate_metadata(Path(key).name, content_type, expected_size)
        destination = self._path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".upload")
        digest = hashlib.sha256()
        total = 0
        try:
            with temporary.open("wb") as target:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > self.max_size_bytes:
                        raise BlobValidationError("attachment exceeds configured maximum size")
                    digest.update(chunk)
                    target.write(chunk)
            if expected_size is not None and total != expected_size:
                raise BlobValidationError("attachment size does not match declaration")
            scan = self.scanner.scan(temporary)
            if scan.status is not MalwareScanStatus.CLEAN:
                quarantine = destination.with_suffix(destination.suffix + ".quarantine")
                temporary.replace(quarantine)
                return BlobWriteResult(
                    quarantine.as_uri(), total, digest.hexdigest(), scan
                )
            temporary.replace(destination)
            return BlobWriteResult(destination.as_uri(), total, digest.hexdigest(), scan)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            close = getattr(chunks, "aclose", None)
            if callable(close):
                await close()

    def open(self, key: str) -> BinaryIO:
        return self._path(key).open("rb")

    def iter_bytes(self, key: str, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        with self.open(key) as source:
            while chunk := source.read(chunk_size):
                yield chunk

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def presign_upload(self, key: str, *, content_type: str, expires_s: int = 900) -> str:
        del content_type, expires_s
        return f"{self.public_base_url}/{safe_blob_key(key).split('/')[-1]}/content"

    def presign_download(self, key: str, *, expires_s: int = 900) -> str:
        del expires_s
        return f"{self.public_base_url}/{safe_blob_key(key).split('/')[-1]}/download"


class MinioBlobStore:
    """Optional S3/MinIO adapter; upload data paths use presigned URLs."""

    def __init__(self, client: MinioClient, bucket: str, *, max_size_bytes: int) -> None:
        self.client = client
        self.bucket = bucket
        self.max_size_bytes = max_size_bytes

    async def put(
        self,
        key: str,
        chunks: AsyncIterable[bytes],
        *,
        content_type: str,
        expected_size: int | None = None,
    ) -> BlobWriteResult:
        del key, chunks, content_type, expected_size
        raise NotImplementedError("use presigned multipart/direct upload for MinIO")

    def open(self, key: str) -> BinaryIO:
        return self.client.get_object(self.bucket, safe_blob_key(key))

    def delete(self, key: str) -> None:
        self.client.remove_object(self.bucket, safe_blob_key(key))

    def presign_upload(self, key: str, *, content_type: str, expires_s: int = 900) -> str:
        del content_type
        return self.client.presigned_put_object(
            self.bucket, safe_blob_key(key), expires=timedelta(seconds=expires_s)
        )

    def presign_download(self, key: str, *, expires_s: int = 900) -> str:
        return self.client.presigned_get_object(
            self.bucket, safe_blob_key(key), expires=timedelta(seconds=expires_s)
        )
