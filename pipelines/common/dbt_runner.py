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

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DBT_PROJECT_DIR = _REPO_ROOT / "dbt"


def run_dbt(
    command: List[str],
    project_dir: Path = DEFAULT_DBT_PROJECT_DIR,
    stage: str = "dbt Transformation",
) -> str:
    """Run `dbt <command...>` against `project_dir`. Returns combined
    stdout on success; raises DbtError (with stage + dbt's own stderr
    tail as `details`) on any non-zero exit."""
    full_command = ["dbt", *command, "--project-dir", str(project_dir)]
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