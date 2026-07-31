"""
tests/unit/test_storage.py

Tests both ObjectStorage implementations against the SAME behavioral
contract (write then read gives back identical bytes; exists reflects
reality; list_keys respects prefix). Running identical test logic against
both LocalFileStorage and S3Storage is what actually proves they're
interchangeable -- the whole point of building this abstraction on Day 8.

S3Storage is tested against a MOCKED S3 backend (moto), not real MinIO --
this sandbox has no Docker daemon (see Day 2's note). Moto proves the
boto3 call logic itself is correct; a live MinIO smoke test is still
something to run on your own machine once `docker compose up` works there.
"""

import boto3
import pytest
from moto import mock_aws

from pipelines.common.config import ConfigError
from pipelines.common.storage import LocalFileStorage, S3Storage, load_minio_storage_from_env


# ---------------------------------------------------------------------------
# Shared contract tests, parametrized over both backends
# ---------------------------------------------------------------------------

@pytest.fixture
def local_storage(tmp_path):
    return LocalFileStorage(tmp_path / "bucket")


@pytest.fixture
def mocked_s3_storage():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="test-bronze-bucket")
        yield S3Storage(
            endpoint_url="https://s3.amazonaws.com",  # moto intercepts regardless of endpoint
            access_key="fake-key",
            secret_key="fake-secret",
            bucket="test-bronze-bucket",
        )


@pytest.fixture(params=["local", "s3"])
def storage(request, local_storage, mocked_s3_storage):
    return local_storage if request.param == "local" else mocked_s3_storage


def test_write_then_read_roundtrips(storage):
    storage.write_bytes("bronze/student/all/data.parquet", b"hello world")
    assert storage.read_bytes("bronze/student/all/data.parquet") == b"hello world"


def test_exists_reflects_reality(storage):
    assert storage.exists("bronze/nope.parquet") is False
    storage.write_bytes("bronze/nope.parquet", b"now it exists")
    assert storage.exists("bronze/nope.parquet") is True


def test_read_missing_key_raises(storage):
    with pytest.raises(Exception):  # FileNotFoundError (local) or botocore ClientError (S3)
        storage.read_bytes("bronze/does/not/exist.parquet")


def test_list_keys_respects_prefix(storage):
    storage.write_bytes("bronze/enrollment/academic_year=2021/semester=1/data.parquet", b"a")
    storage.write_bytes("bronze/enrollment/academic_year=2021/semester=2/data.parquet", b"b")
    storage.write_bytes("bronze/student/all/data.parquet", b"c")

    enrollment_keys = storage.list_keys("bronze/enrollment")
    assert len(enrollment_keys) == 2
    assert all("enrollment" in k for k in enrollment_keys)

    all_keys = storage.list_keys("bronze")
    assert len(all_keys) == 3


def test_list_keys_empty_prefix_returns_empty_list(storage):
    assert storage.list_keys("bronze/nothing/here") == []


# ---------------------------------------------------------------------------
# stat() -- physical object metadata for Bronze-existence auditing
# ---------------------------------------------------------------------------

def test_stat_returns_key_size_and_last_modified(storage):
    payload = b"hello world"
    storage.write_bytes("bronze/student/all/data.parquet", payload)

    meta = storage.stat("bronze/student/all/data.parquet")

    assert meta.key == "bronze/student/all/data.parquet"
    assert meta.size_bytes == len(payload)
    assert meta.last_modified is not None


def test_stat_missing_key_raises_file_not_found(storage):
    with pytest.raises(FileNotFoundError):
        storage.stat("bronze/does/not/exist.parquet")


def test_stat_zero_byte_object_reports_zero_size(storage):
    storage.write_bytes("bronze/student/all/empty.parquet", b"")
    meta = storage.stat("bronze/student/all/empty.parquet")
    assert meta.size_bytes == 0


# ---------------------------------------------------------------------------
# load_minio_storage_from_env
# ---------------------------------------------------------------------------

def test_load_minio_storage_from_env_missing_vars_raises_config_error():
    with pytest.raises(ConfigError, match="Missing required environment variable"):
        load_minio_storage_from_env("MINIO_BRONZE_BUCKET", env={})


def test_load_minio_storage_from_env_builds_s3storage():
    env = {
        "MINIO_ENDPOINT": "localhost:9000",
        "MINIO_ROOT_USER": "admin",
        "MINIO_ROOT_PASSWORD": "secret",
        "MINIO_BRONZE_BUCKET": "bronze",
    }
    result = load_minio_storage_from_env("MINIO_BRONZE_BUCKET", env=env)
    assert isinstance(result, S3Storage)
    assert result.bucket == "bronze"