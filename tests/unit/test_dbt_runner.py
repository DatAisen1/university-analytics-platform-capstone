"""
tests/unit/test_dbt_runner.py

P0.44/P0.50 regression coverage for pipelines/common/dbt_runner.py:

- `run_dbt` must always pass an explicit `--profiles-dir`, resolved from
  DBTSettings.DBT_PROFILES_DIR against the repo root -- not left to the
  caller's process environment already having DBT_PROFILES_DIR exported
  (Dagster's daemon process has no guarantee of that), and not left
  relative to dbt_runner's own subprocess cwd (.../dbt), which would
  silently resolve the default "dbt" setting to .../dbt/dbt.
- A non-zero dbt exit code must raise DbtError (fail loudly), not be
  swallowed -- this is what lets Dagster's `dbt` asset correctly block
  the downstream `features` asset (P0.50: failure propagation).

Entirely subprocess-mocked: no real dbt CLI, Postgres, or network
required.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from pipelines.common.dbt_runner import _REPO_ROOT, DEFAULT_DBT_PROJECT_DIR, run_dbt
from pipelines.common.errors import DbtError


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["dbt"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_dbt_passes_explicit_profiles_dir_resolved_against_repo_root(monkeypatch):
    monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)  # exercise the "dbt" default

    captured_command = {}

    def _fake_run(command, **kwargs):
        captured_command["command"] = command
        if kwargs.get("check"):
            return _completed(0, stdout="dbt run succeeded")
        return _completed(0)

    with patch("pipelines.common.dbt_runner.subprocess.run", side_effect=_fake_run):
        run_dbt(["run"])

    command = captured_command["command"]
    assert "--profiles-dir" in command
    profiles_dir_value = command[command.index("--profiles-dir") + 1]
    assert Path(profiles_dir_value) == _REPO_ROOT / "dbt"
    # Must not be resolved relative to the subprocess's own cwd
    # (DEFAULT_DBT_PROJECT_DIR), which would produce .../dbt/dbt.
    assert Path(profiles_dir_value) != DEFAULT_DBT_PROJECT_DIR / "dbt"


def test_run_dbt_respects_explicit_dbt_profiles_dir_env_var(monkeypatch, tmp_path):
    custom_dir = tmp_path / "custom_profiles"
    monkeypatch.setenv("DBT_PROFILES_DIR", str(custom_dir))

    captured_command = {}

    def _fake_run(command, **kwargs):
        captured_command["command"] = command
        return _completed(0)

    with patch("pipelines.common.dbt_runner.subprocess.run", side_effect=_fake_run):
        run_dbt(["test"])

    command = captured_command["command"]
    profiles_dir_value = command[command.index("--profiles-dir") + 1]
    assert Path(profiles_dir_value) == custom_dir


def test_run_dbt_raises_dbt_error_on_nonzero_exit(monkeypatch):
    monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)

    def _fake_run(command, **kwargs):
        raise subprocess.CalledProcessError(
            returncode=1, cmd=command, output="", stderr="Compilation Error in model stg_dim_student"
        )

    with patch("pipelines.common.dbt_runner.subprocess.run", side_effect=_fake_run):
        with pytest.raises(DbtError) as exc_info:
            run_dbt(["run"], stage="dbt run")

    assert exc_info.value.stage == "dbt run"
    assert exc_info.value.category.value == "DBT_ERROR"
    assert "Compilation Error" in exc_info.value.details["stderr_tail"]


def test_run_dbt_raises_dbt_error_when_cli_missing(monkeypatch):
    monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)

    def _fake_run(command, **kwargs):
        raise FileNotFoundError("dbt not found")

    with patch("pipelines.common.dbt_runner.subprocess.run", side_effect=_fake_run):
        with pytest.raises(DbtError):
            run_dbt(["run"])