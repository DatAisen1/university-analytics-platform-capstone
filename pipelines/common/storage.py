"""
pipelines/common/storage.py

A small object-storage abstraction so ingestion/pipeline code depends on
an interface (ObjectStorage), never on a specific backend. Two
implementations:

  - LocalFileStorage: writes to the local filesystem. Used for actual
    development and testing in this environment, which has no Docker
    daemon and therefore no running MinIO container (see docs/02's
    deployment view and the Day 2 note that this sandbox can't run
    containers).
  - S3Storage: real boto3-backed implementation, compatible with MinIO
    (which speaks the S3 API) or genuine AWS S3. Tested here against a
    MOCKED S3 backend (moto), which proves the boto3 logic itself is
    correct without needing a live MinIO container -- but a live MinIO
    smoke test (docker compose up, then point S3Storage at it) is still
    something only you can run, on your machine, per the same caveat as
    Day 2's docker-compose.yml.

Why this split matters: ingestion code (pipelines/ingestion/ingest_to_bronze.py)
is written against ObjectStorage only. Swapping LocalFileStorage for
S3Storage in production is a one-line change at the call site, not a
rewrite of ingestion logic -- this is the entire point of depending on an
interface instead of a concrete backend.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from pipelines.common.errors import MinioError


@dataclass(frozen=True)
class ObjectMetadata:
    """Physical facts about a stored object -- what a Bronze-existence
    audit needs (Task 19: verify bucket / object path / file name / file
    size / timestamp), as opposed to trusting that a script exiting
    without an exception means data landed. `bucket` is None for
    LocalFileStorage, since a filesystem has no bucket concept."""

    key: str
    size_bytes: int
    last_modified: datetime
    bucket: Optional[str] = None


class ObjectStorage(ABC):
    """Minimal key-value object storage interface. Keys use '/' as a
    path-like separator (e.g. 'bronze/enrollment/academic_year=2021/semester=1/data.parquet'),
    mirroring how S3/MinIO keys work."""

    @abstractmethod
    def write_bytes(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def read_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def list_keys(self, prefix: str) -> List[str]: ...

    @abstractmethod
    def stat(self, key: str) -> ObjectMetadata:
        """Return size/last-modified/bucket for `key` without reading its
        body. Raises FileNotFoundError if the key does not exist -- the
        same exception type on every backend, so audit code (Task 19)
        doesn't need backend-specific except clauses."""
        ...


class LocalFileStorage(ObjectStorage):
    """Filesystem-backed ObjectStorage: `key` maps directly onto a path
    under `base_path`. Used in this environment in place of MinIO."""

    def __init__(self, base_path: Path):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        return self.base_path / key

    def write_bytes(self, key: str, data: bytes) -> None:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read_bytes(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.exists():
            raise FileNotFoundError(f"No object at key: {key}")
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._resolve(key).exists()

    def list_keys(self, prefix: str) -> List[str]:
        prefix_path = self._resolve(prefix)
        if not prefix_path.exists():
            return []
        if prefix_path.is_file():
            return [prefix]
        return [
            str(p.relative_to(self.base_path)).replace(os.sep, "/")
            for p in prefix_path.rglob("*") if p.is_file()
        ]

    def stat(self, key: str) -> ObjectMetadata:
        path = self._resolve(key)
        if not path.exists():
            raise FileNotFoundError(f"No object at key: {key}")
        info = path.stat()
        return ObjectMetadata(
            key=key,
            size_bytes=info.st_size,
            last_modified=datetime.fromtimestamp(info.st_mtime, tz=timezone.utc),
            bucket=None,
        )


class S3Storage(ObjectStorage):
    """boto3-backed ObjectStorage, compatible with MinIO (S3 API) or AWS
    S3. Requires the `bucket` to already exist -- bucket creation is an
    infra concern (docker-compose / MinIO console), not something
    ingestion code should do implicitly on every run.
    """

    def __init__(self, endpoint_url: str, access_key: str, secret_key: str, bucket: str):
        import boto3  # imported lazily so environments without boto3 (unlikely, but defensive) don't fail at module import time

        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    def write_bytes(self, key: str, data: bytes) -> None:
        """Write `data` to `key`, then read it back to confirm it's really
        there (Task 55). A successful `put_object` call only means MinIO/S3
        accepted and acknowledged the request -- it is NOT proof the object
        is visible afterward (a script that "completed successfully" is not
        the same claim as "the data exists in MinIO"; see e.g. eventual-
        consistency edge cases, silent client/network issues, or a bucket
        policy quirk that accepts writes but serves stale reads). This
        write-then-verify round trip is the only way to actually know."""
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            self._client.put_object(Bucket=self.bucket, Key=key, Body=data)
        except (BotoCoreError, ClientError) as exc:
            raise MinioError(
                f"Failed to write object to MinIO/S3: {exc}",
                stage="Object Storage Write", entity=key, details={"bucket": self.bucket},
            ) from exc

        try:
            verified = self.stat(key)
        except (FileNotFoundError, BotoCoreError, ClientError) as exc:
            raise MinioError(
                f"Write to MinIO/S3 appeared to succeed but the object could not be "
                f"verified afterward (head_object failed): {exc}",
                stage="Object Storage Write Verification", entity=key, details={"bucket": self.bucket},
            ) from exc

        if verified.size_bytes != len(data):
            raise MinioError(
                f"Write to MinIO/S3 was verified but the stored size ({verified.size_bytes} "
                f"bytes) does not match what was written ({len(data)} bytes) -- treating "
                f"this as a failed write rather than trusting put_object's success response.",
                stage="Object Storage Write Verification", entity=key, details={"bucket": self.bucket},
            )

    def read_bytes(self, key: str) -> bytes:
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()
        except (BotoCoreError, ClientError) as exc:
            raise MinioError(
                f"Failed to read object from MinIO/S3: {exc}",
                stage="Object Storage Read", entity=key, details={"bucket": self.bucket},
            ) from exc

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
                return False
            raise

    def list_keys(self, prefix: str) -> List[str]:
        keys: List[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    def stat(self, key: str) -> ObjectMetadata:
        from botocore.exceptions import ClientError

        try:
            response = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
                raise FileNotFoundError(f"No object at key: {key}") from exc
            raise
        return ObjectMetadata(
            key=key,
            size_bytes=response["ContentLength"],
            last_modified=response["LastModified"],
            bucket=self.bucket,
        )


def load_minio_storage_from_env(bucket_env_var: str, env: Optional[dict] = None) -> S3Storage:
    """Build an S3Storage pointed at MinIO using pipelines.common.settings'
    centralized MinioSettings domain (P0.12). `bucket_env_var` is which
    bucket-name field to use, e.g. 'MINIO_BRONZE_BUCKET'. `env` is
    forwarded to settings.get_minio_settings -- pass an explicit mapping
    (as tests do) to bypass the real process environment.
    """
    from pipelines.common.settings import get_minio_settings

    settings = get_minio_settings(env)
    return S3Storage(
        endpoint_url=settings.endpoint_url,
        access_key=settings.MINIO_ROOT_USER,
        secret_key=settings.MINIO_ROOT_PASSWORD,
        bucket=settings.bucket_for(bucket_env_var),
    )