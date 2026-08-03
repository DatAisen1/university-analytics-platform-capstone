"""
scripts/create_minio_buckets.py

Creates the bronze/silver/gold buckets in MinIO if they don't already
exist. Nothing in docker-compose.yml does this automatically (no init
container), and S3Storage deliberately refuses to create a bucket
implicitly on write -- see storage.py's S3Storage docstring: bucket
creation is an infra concern, not something ingestion code should do
silently on every run. This script is that explicit, one-time (or
after every `docker compose down -v`, which wipes the MinIO data
volume) infra step.

Idempotent: safe to re-run. An already-existing bucket is reported, not
treated as an error.

Usage (after `make up` / `make clean-start`, with .env populated):
    python -m scripts.create_minio_buckets
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

from pipelines.common.storage import load_minio_storage_from_env

_REPO_ROOT = Path(__file__).resolve().parents[1]
BUCKET_ENV_VARS = ["MINIO_BRONZE_BUCKET", "MINIO_SILVER_BUCKET", "MINIO_GOLD_BUCKET"]


def main() -> None:
    load_dotenv(_REPO_ROOT / ".env")

    # Reuse the project's own env-loading path (fails fast with a clear
    # ConfigError if any MinIO env var is missing) rather than reading
    # os.environ directly here -- one place defines "how MinIO config is
    # loaded," same as the rest of this codebase.
    for bucket_env_var in BUCKET_ENV_VARS:
        storage = load_minio_storage_from_env(bucket_env_var)
        client = storage._client  # same boto3 client S3Storage itself uses
        bucket_name = storage.bucket
        try:
            client.head_bucket(Bucket=bucket_name)
            print(f"  {bucket_name}: already exists")
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code not in ("404", "NoSuchBucket"):
                raise
            client.create_bucket(Bucket=bucket_name)
            print(f"  {bucket_name}: created")


if __name__ == "__main__":
    main()