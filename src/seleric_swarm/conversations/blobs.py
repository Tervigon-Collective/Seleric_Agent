"""Secure streaming blob storage used by conversation attachments."""

from __future__ import annotations

import hashlib
import mimetypes
import tempfile
from collections.abc import AsyncIterable, Iterator
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Protocol, cast


class BlobValidationError(ValueError):
    pass


class MalwareScanStatus(StrEnum):
    PENDING = "PENDING"
    CLEAN = "CLEAN"
    QUARANTINED = "QUARANTINED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class MalwareScanResult:
    status: MalwareScanStatus
    detail: str = ""


class MalwareScanner(Protocol):
    def scan(self, path: Path) -> MalwareScanResult: ...


class UnavailableMalwareScanner:
    """Fail-closed scanner used when no malware scanner is configured."""

    def scan(self, path: Path) -> MalwareScanResult:
        del path
        return MalwareScanResult(MalwareScanStatus.UNAVAILABLE, "scanner not configured")


# Compatibility import for callers that previously selected the default scanner explicitly.
NoOpMalwareScanner = UnavailableMalwareScanner


@dataclass(frozen=True)
class BlobWriteResult:
    uri: str
    size_bytes: int
    checksum_sha256: str
    scan: MalwareScanResult

    @property
    def ready(self) -> bool:
        """Only a verified successful scan permits READY attachment state."""
        return self.scan.status is MalwareScanStatus.CLEAN


class BlobStore(Protocol):
    async def put(
        self,
        key: str,
        chunks: AsyncIterable[bytes],
        *,
        content_type: str,
        expected_size: int | None = None,
    ) -> BlobWriteResult: ...

    def get(self, key: str) -> BinaryIO: ...
    def open(self, key: str) -> BinaryIO: ...
    def delete(self, key: str) -> None: ...
    def presign_upload(self, key: str, *, content_type: str, expires_s: int = 900) -> str: ...
    def presign_download(self, key: str, *, expires_s: int = 900) -> str: ...


class MinioClient(Protocol):
    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data: BinaryIO,
        length: int,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str | list[str] | tuple[str]] | None = None,
    ) -> Any: ...
    def get_object(self, bucket_name: str, object_name: str) -> Any: ...
    def stat_object(self, bucket_name: str, object_name: str) -> Any: ...
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
    if (
        not normalized
        or path.is_absolute()
        or ".." in path.parts
        or any(p in {"", "."} for p in path.parts)
    ):
        raise BlobValidationError("invalid blob key")
    return str(path)


DEFAULT_ALLOWED_MIME_TYPES = frozenset(
    {
        "application/json",
        "application/pdf",
        "text/csv",
        "text/plain",
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)


def normalize_content_type(content_type: str) -> str:
    clean_type = content_type.split(";", 1)[0].strip().lower()
    if not clean_type or "/" not in clean_type:
        raise BlobValidationError("invalid MIME type")
    return clean_type


def validate_blob_metadata(
    filename: str,
    content_type: str,
    size_bytes: int | None,
    *,
    max_size_bytes: int,
    allowed_mime_types: set[str] | frozenset[str],
) -> str:
    """Validate adapter-neutral upload metadata and return normalized MIME."""
    clean_type = normalize_content_type(content_type)
    if clean_type not in allowed_mime_types:
        raise BlobValidationError(f"MIME type is not allowed: {clean_type}")
    if size_bytes is not None and (size_bytes < 0 or size_bytes > max_size_bytes):
        raise BlobValidationError("attachment exceeds configured maximum size")
    guessed, _ = mimetypes.guess_type(filename)
    if guessed and clean_type.startswith(("image/", "text/")) and guessed != clean_type:
        raise BlobValidationError("filename extension does not match MIME type")
    return clean_type


def verify_blob_size(actual_size: int, expected_size: int | None, max_size_bytes: int) -> None:
    if actual_size < 0:
        raise BlobValidationError("blob size is invalid")
    if actual_size > max_size_bytes:
        raise BlobValidationError("attachment exceeds configured maximum size")
    if expected_size is not None and actual_size != expected_size:
        raise BlobValidationError("attachment size does not match declaration")


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
        self.allowed_mime_types = allowed_mime_types or set(DEFAULT_ALLOWED_MIME_TYPES)
        self.scanner = scanner or UnavailableMalwareScanner()
        self.public_base_url = public_base_url.rstrip("/")

    def _path(self, key: str) -> Path:
        candidate = (self.root / safe_blob_key(key)).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise BlobValidationError("blob path escapes storage root")
        return candidate

    def validate_metadata(self, filename: str, content_type: str, size_bytes: int | None) -> None:
        validate_blob_metadata(
            filename,
            content_type,
            size_bytes,
            max_size_bytes=self.max_size_bytes,
            allowed_mime_types=self.allowed_mime_types,
        )

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
            verify_blob_size(total, expected_size, self.max_size_bytes)
            scan = self.scanner.scan(temporary)
            if scan.status is not MalwareScanStatus.CLEAN:
                quarantine = destination.with_suffix(destination.suffix + ".quarantine")
                temporary.replace(quarantine)
                return BlobWriteResult(quarantine.as_uri(), total, digest.hexdigest(), scan)
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
        return self.get(key)

    def get(self, key: str) -> BinaryIO:
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
    """S3/MinIO adapter with server-computed checksums and fail-closed scanning."""

    def __init__(
        self,
        client: MinioClient,
        bucket: str,
        *,
        max_size_bytes: int,
        allowed_mime_types: set[str] | None = None,
        scanner: MalwareScanner | None = None,
    ) -> None:
        self.client = client
        self.bucket = bucket
        self.max_size_bytes = max_size_bytes
        self.allowed_mime_types = allowed_mime_types or set(DEFAULT_ALLOWED_MIME_TYPES)
        self.scanner = scanner or UnavailableMalwareScanner()

    async def put(
        self,
        key: str,
        chunks: AsyncIterable[bytes],
        *,
        content_type: str,
        expected_size: int | None = None,
    ) -> BlobWriteResult:
        object_name = safe_blob_key(key)
        clean_type = validate_blob_metadata(
            Path(object_name).name,
            content_type,
            expected_size,
            max_size_bytes=self.max_size_bytes,
            allowed_mime_types=self.allowed_mime_types,
        )
        digest = hashlib.sha256()
        total = 0
        temporary_path: Path | None = None
        uploaded = False
        try:
            with tempfile.NamedTemporaryFile("w+b", delete=False) as temporary:
                temporary_path = Path(temporary.name)
                async for chunk in chunks:
                    if not chunk:
                        continue
                    total += len(chunk)
                    verify_blob_size(total, None, self.max_size_bytes)
                    digest.update(chunk)
                    temporary.write(chunk)
                verify_blob_size(total, expected_size, self.max_size_bytes)
                temporary.flush()
            checksum = digest.hexdigest()
            scan = self.scanner.scan(temporary_path)
            if scan.status is not MalwareScanStatus.CLEAN:
                return BlobWriteResult(
                    f"quarantine://{self.bucket}/{object_name}",
                    total,
                    checksum,
                    scan,
                )
            # MinIO's official client is synchronous and requires a blocking file object.
            with temporary_path.open("rb") as source:  # noqa: ASYNC230
                self.client.put_object(
                    self.bucket,
                    object_name,
                    source,
                    total,
                    clean_type,
                    {"sha256": checksum},
                )
                uploaded = True
            self._verify_remote_metadata(object_name, total, clean_type, checksum)
            return BlobWriteResult(
                f"s3://{self.bucket}/{object_name}",
                total,
                checksum,
                scan,
            )
        except Exception:
            # A partially accepted or unverifiable object must never remain readable.
            if uploaded:
                self.client.remove_object(self.bucket, object_name)
            raise
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            close = getattr(chunks, "aclose", None)
            if callable(close):
                await close()

    def open(self, key: str) -> BinaryIO:
        return self.get(key)

    def get(self, key: str) -> BinaryIO:
        object_name = safe_blob_key(key)
        stat = self.client.stat_object(self.bucket, object_name)
        size = int(getattr(stat, "size", -1))
        verify_blob_size(size, None, self.max_size_bytes)
        content_type = normalize_content_type(str(getattr(stat, "content_type", "")))
        if content_type not in self.allowed_mime_types:
            raise BlobValidationError("stored blob MIME type is not allowed")
        metadata = self._metadata(stat)
        checksum = metadata.get("sha256") or metadata.get("x-amz-meta-sha256", "")
        if len(checksum) != 64 or any(
            character not in "0123456789abcdef" for character in checksum
        ):
            raise BlobValidationError("stored blob checksum metadata is invalid")
        response = self.client.get_object(self.bucket, object_name)
        if not hasattr(response, "read"):
            raise BlobValidationError("blob client returned a non-readable object")
        return cast(BinaryIO, response)

    def delete(self, key: str) -> None:
        self.client.remove_object(self.bucket, safe_blob_key(key))

    def _verify_remote_metadata(
        self, key: str, size_bytes: int, content_type: str, checksum_sha256: str
    ) -> None:
        stat = self.client.stat_object(self.bucket, key)
        if int(getattr(stat, "size", -1)) != size_bytes:
            raise BlobValidationError("stored blob size verification failed")
        stored_type = normalize_content_type(str(getattr(stat, "content_type", "")))
        if stored_type != content_type:
            raise BlobValidationError("stored blob MIME verification failed")
        metadata = self._metadata(stat)
        stored_checksum = metadata.get("sha256") or metadata.get("x-amz-meta-sha256")
        if stored_checksum != checksum_sha256:
            raise BlobValidationError("stored blob checksum verification failed")

    @staticmethod
    def _metadata(stat: object) -> dict[str, str]:
        return {
            str(name).lower(): str(value).lower()
            for name, value in dict(getattr(stat, "metadata", {}) or {}).items()
        }

    def presign_upload(self, key: str, *, content_type: str, expires_s: int = 900) -> str:
        del content_type
        return self.client.presigned_put_object(
            self.bucket, safe_blob_key(key), expires=timedelta(seconds=expires_s)
        )

    def presign_download(self, key: str, *, expires_s: int = 900) -> str:
        return self.client.presigned_get_object(
            self.bucket, safe_blob_key(key), expires=timedelta(seconds=expires_s)
        )
