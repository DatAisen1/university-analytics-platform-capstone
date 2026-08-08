"""
pipelines/common/dbt_runner.py

Thin subprocess wrapper around the dbt CLI so any dbt invocation raises
a categorized DbtError (Task 46: DBT_ERROR) instead of a raw
subprocess.CalledProcessError with no stage/rows_affected context.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional

from pipelines.common.errors import DbtError
from pipelines.common.settings import get_dbt_settings

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DBT_PROJECT_DIR = _REPO_ROOT / "dbt"


def _resolve_profiles_dir() -> Path:
    """Resolve DBTSettings.DBT_PROFILES_DIR (default "dbt") against the
    repo root -- NOT against DEFAULT_DBT_PROJECT_DIR / the subprocess's
    own cwd, which would silently resolve the default to .../dbt/dbt.
    An already-absolute DBT_PROFILES_DIR (e.g. a test's tmp_path, or an
    operator-supplied override) is returned unchanged."""
    raw = get_dbt_settings().DBT_PROFILES_DIR
    profiles_dir = Path(raw)
    if not profiles_dir.is_absolute():
        profiles_dir = _REPO_ROOT / profiles_dir
    return profiles_dir


def run_dbt(
    command: List[str],
    project_dir: Path = DEFAULT_DBT_PROJECT_DIR,
    stage: str = "dbt Transformation",
) -> str:
    """Run `dbt <command...>` against `project_dir`. Always passes an
    explicit `--profiles-dir` (resolved via DBTSettings) instead of
    relying on the caller's process environment already having
    DBT_PROFILES_DIR exported -- Dagster's daemon process has no
    guarantee of that. Returns combined stdout on success; raises
    DbtError (with stage + dbt's own stderr tail as `details`) on any
    non-zero exit."""
    profiles_dir = _resolve_profiles_dir()
    full_command = [
        "dbt", *command,
        "--project-dir", str(project_dir),
        "--profiles-dir", str(profiles_dir),
    ]
    try:
        result = subprocess.run(
            full_command, cwd=project_dir, capture_output=True, text=True, check=True,
        )
        return result.stdout
    except FileNotFoundError as exc:
        raise DbtError(
            "dbt CLI not found on PATH", stage=stage, details={"command": " ".join(full_command)},
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise DbtError(
            f"dbt command failed (exit code {exc.returncode}): {' '.join(full_command)}",
            stage=stage,
            details={"stderr_tail": (exc.stderr or "")[-1000:]},
        ) from exc