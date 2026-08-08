"""
scripts/verify_minio_data.py

Task 55: fix MinIO data visibility. The bug this closes: pipeline code
(pipelines/ingestion/ingest_to_bronze.py etc.) exiting with status 0 was
being treated as equivalent to "the data is in MinIO" -- but a clean exit
only proves the *script* didn't raise, not that the object landed and is
now readable. This script is the independent, out-of-band check: it never
trusts anything the pipeline itself reported, and confirms data through
THREE separate channels so no single tool's blind spot goes unnoticed:

  1. Python client (this script, via boto3)   -- run directly below
  2. CLI (MinIO's `mc`)                        -- printed commands, run separately
  3. Web Console                               -- printed URL + steps, checked by a human

Companion code fix: pipelines/common/storage.py's S3Storage.write_bytes
now verifies every write with a read-back (head_object) immediately after
put_object, so an object failing to actually persist raises MinioError
right where the write happened, rather than being silently discovered
later (or never) by an audit like this one.

Usage (after `docker compose up -d` / `make up`, with .env populated):
    python3 scripts/verify_minio_data.py
    python3 scripts/verify_minio_data.py --bucket bronze --prefix student
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipelines.common.settings import MinioSettings, SettingsError, get_minio_settings


@dataclass
class BucketReport:
    bucket: str
    object_count: int
    total_bytes: int
    sample_keys: List[str]


def _load_settings() -> MinioSettings:
    # Reuse the project's own centralized settings (pipelines.common.settings)
    # rather than reading os.environ directly here -- one place defines "how
    # MinIO config is loaded," same as the rest of this codebase (P0.12).
    try:
        return get_minio_settings()
    except SettingsError as exc:
        print(f"{exc.message}")
        print("Run: cp .env.example .env (if you haven't), fill in real values, then:")
        print("    export $(grep -v '^#' .env | xargs)")
        print("before running this script -- or run it via `make verify-minio`.")
        sys.exit(1)


def _build_client(endpoint: str, access_key: str, secret_key: str):
    import boto3  # local import: this script is the one place a bare `python3 scripts/...`
    #                invocation needs boto3 available; the rest of the pipeline imports it lazily too.

    url = endpoint if endpoint.startswith("http") else f"http://{endpoint}"
    return boto3.client(
        "s3",
        endpoint_url=url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


# ---------------------------------------------------------------------------
# Channel 1: Python client
# ---------------------------------------------------------------------------

def verify_via_python_client(client, bucket: str, prefix: str = "") -> BucketReport:
    """The only channel this script can run for you automatically. Lists
    real objects via ListObjectsV2 -- NOT `docker compose ps`, NOT a
    pipeline's own "success" log line, NOT bucket *existence* alone (an
    empty bucket "exists" too). Object count and total size are the actual
    evidence of data, not a proxy for it."""
    paginator = client.get_paginator("list_objects_v2")
    object_count = 0
    total_bytes = 0
    sample_keys: List[str] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            object_count += 1
            total_bytes += obj["Size"]
            if len(sample_keys) < 10:
                sample_keys.append(f"{obj['Key']} ({obj['Size']} bytes, modified {obj['LastModified']})")
    return BucketReport(bucket=bucket, object_count=object_count, total_bytes=total_bytes, sample_keys=sample_keys)


# ---------------------------------------------------------------------------
# Channel 2 & 3: printed instructions -- these require a human or a
# separately-run CLI, so this script cannot silently claim to have "done"
# them; it prints exactly what to run/click so nothing gets skipped.
# ---------------------------------------------------------------------------

def print_cli_instructions(endpoint: str, buckets: List[str]) -> None:
    print("== Channel 2: MinIO CLI (mc) ==")
    print("  If `mc` isn't installed: https://min.io/docs/minio/linux/reference/minio-mc.html")
    print(f"  mc alias set uap_local http://{endpoint} $MINIO_ROOT_USER $MINIO_ROOT_PASSWORD")
    for bucket in buckets:
        print(f"  mc ls uap_local/{bucket} --recursive")
    print("  Confirm the object count/sizes here MATCH the Python client output above --")
    print("  a mismatch means the two clients disagree about what's actually stored.")
    print()


def print_console_instructions(console_url: str, buckets: List[str]) -> None:
    print("== Channel 3: MinIO Web Console ==")
    print(f"  Open {console_url} and log in with MINIO_ROOT_USER / MINIO_ROOT_PASSWORD from .env")
    for bucket in buckets:
        print(f"  Navigate to bucket '{bucket}' and confirm objects are listed with non-zero sizes")
    print("  (a bucket that 'exists' but is empty will still show up here -- check contents, not just presence)")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify data actually exists in MinIO (Task 55).")
    parser.add_argument(
        "--bucket",
        action="append",
        dest="buckets",
        help="Bucket to check (repeatable). Defaults to bronze/silver/gold from .env.",
    )
    parser.add_argument("--prefix", default="", help="Optional key prefix to filter within each bucket.")
    args = parser.parse_args()

    settings = _load_settings()
    buckets = args.buckets or [
        settings.MINIO_BRONZE_BUCKET,
        settings.MINIO_SILVER_BUCKET,
        settings.MINIO_GOLD_BUCKET,
    ]

    client = _build_client(settings.endpoint_url, settings.MINIO_ROOT_USER, settings.MINIO_ROOT_PASSWORD)

    print("== Channel 1: Python client (boto3) -- ground truth for this run ==")
    any_empty = False
    for bucket in buckets:
        try:
            report = verify_via_python_client(client, bucket, args.prefix)
        except Exception as exc:  # noqa: BLE001 -- surfacing the raw client error is the point here
            print(f"  {bucket}: FAILED to query -- {exc}")
            any_empty = True
            continue

        print(f"  {bucket}: {report.object_count} object(s), {report.total_bytes} total bytes")
        for sample in report.sample_keys:
            print(f"    - {sample}")
        if report.object_count == 0:
            print(f"    NOTE: '{bucket}' has zero objects -- do not assume a prior pipeline run wrote data here.")
            any_empty = True
    print()

    console_host = settings.MINIO_ENDPOINT.replace("http://", "").replace("https://", "").split(":")[0]
    print_cli_instructions(settings.MINIO_ENDPOINT, buckets)
    print_console_instructions(f"http://{console_host}:{settings.MINIO_CONSOLE_PORT}", buckets)

    if any_empty:
        print("RESULT: at least one bucket had no objects or could not be queried -- do NOT treat a")
        print("        prior pipeline run's exit code as proof this data exists. Investigate above.")
        return 1

    print("RESULT: Python client confirms objects exist in every checked bucket.")
    print("        Still cross-check the CLI/Console commands above before trusting this alone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())