"""
pipelines/common/config.py

Loads and validates the university's reference data (colleges, programs)
from configs/colleges.yaml and configs/programs.yaml.

Why this file exists at all, instead of just `yaml.safe_load(...)` at the
call site: reference data has two distinct kinds of things that can go wrong,
and this module deliberately separates them rather than discovering both as
one big pile of stack traces:

1. SHAPE errors  — the YAML doesn't match the schema we expect
                    (missing field, wrong type, unknown program_level value).
                    Caught by pydantic model validation.
2. RELATIONSHIP errors — every individual record is shaped correctly, but the
                    *set* of records is inconsistent (a program points at a
                    college_id that doesn't exist, or two colleges share an
                    ID). Caught by explicit cross-reference checks after
                    pydantic validation succeeds.

Conflating these two would mean a program-references-unknown-college bug
surfaces as a confusing pydantic traceback instead of a clear, actionable
message — which is exactly the kind of error message a data engineer
shouldn't have to reverse-engineer at 11pm before a demo.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Dict, List
from pipelines.common.errors import InvalidSchemaError
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ConfigError(InvalidSchemaError):
    """Raised for any reference-data config problem: missing file,
    malformed YAML, schema violation, or cross-reference violation.
    Now a subclass of InvalidSchemaError (Task 46) -- callers that
    already do `except ConfigError:` are unaffected; new code can catch
    `PipelineError` uniformly and get category=INVALID_SCHEMA."""

    def __init__(self, message: str, *, stage: str = "Reference Data Config", **kwargs):
        super().__init__(message, stage=stage, **kwargs)
class ProgramLevel(str, Enum):
    BACHELOR = "Bachelor"
    CERTIFICATE = "Certificate"
    DIPLOMA = "Diploma"


class College(BaseModel):
    model_config = ConfigDict(extra="forbid")  # unknown keys are a config bug, not a typo to silently ignore

    college_id: str = Field(min_length=1)
    college_name: str = Field(min_length=1)


class Program(BaseModel):
    model_config = ConfigDict(extra="forbid")

    program_id: str = Field(min_length=1)
    program_name: str = Field(min_length=1)
    college_id: str = Field(min_length=1)
    program_level: ProgramLevel
    nominal_duration_years: float = Field(gt=0, le=10)


class _CollegesFile(BaseModel):
    """Shape of configs/colleges.yaml."""
    version: int
    colleges: List[College]


class _ProgramsFile(BaseModel):
    """Shape of configs/programs.yaml."""
    version: int
    programs: List[Program]


class ReferenceData(BaseModel):
    """Validated, cross-referenced reference data — the object the rest of
    the pipeline should depend on. Once this object exists, every program
    is guaranteed to point at a real college and every ID is guaranteed unique."""

    colleges: List[College]
    programs: List[Program]

    def college_by_id(self, college_id: str) -> College:
        for c in self.colleges:
            if c.college_id == college_id:
                return c
        raise KeyError(f"Unknown college_id: {college_id!r}")

    def programs_for_college(self, college_id: str) -> List[Program]:
        return [p for p in self.programs if p.college_id == college_id]

    def as_college_lookup(self) -> Dict[str, College]:
        return {c.college_id: c for c in self.colleges}

    def as_program_lookup(self) -> Dict[str, Program]:
        return {p.program_id: p for p in self.programs}


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            content = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Malformed YAML in {path}: {exc}") from exc

    if content is None:
        raise ConfigError(f"Config file is empty: {path}")
    if not isinstance(content, dict):
        raise ConfigError(
            f"Config file {path} must contain a YAML mapping at the top "
            f"level, got {type(content).__name__}"
        )
    return content


def load_colleges(path: Path) -> List[College]:
    """Load and shape-validate configs/colleges.yaml."""
    raw = _load_yaml(path)
    try:
        parsed = _CollegesFile.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid colleges config at {path}:\n{exc}") from exc
    return parsed.colleges


def load_programs(path: Path) -> List[Program]:
    """Load and shape-validate configs/programs.yaml."""
    raw = _load_yaml(path)
    try:
        parsed = _ProgramsFile.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid programs config at {path}:\n{exc}") from exc
    return parsed.programs


def load_reference_data(colleges_path: Path, programs_path: Path) -> ReferenceData:
    """Load colleges + programs and validate them together.

    Raises ConfigError with a specific, actionable message if:
      - either file is missing / malformed / fails shape validation
      - college_id is duplicated within colleges.yaml
      - program_id is duplicated within programs.yaml
      - any program references a college_id that doesn't exist in colleges.yaml
    """
    colleges = load_colleges(colleges_path)
    programs = load_programs(programs_path)

    college_ids = [c.college_id for c in colleges]
    duplicate_college_ids = {cid for cid in college_ids if college_ids.count(cid) > 1}
    if duplicate_college_ids:
        raise ConfigError(
            f"Duplicate college_id(s) in {colleges_path}: {sorted(duplicate_college_ids)}"
        )

    program_ids = [p.program_id for p in programs]
    duplicate_program_ids = {pid for pid in program_ids if program_ids.count(pid) > 1}
    if duplicate_program_ids:
        raise ConfigError(
            f"Duplicate program_id(s) in {programs_path}: {sorted(duplicate_program_ids)}"
        )

    known_college_ids = set(college_ids)
    orphaned = sorted(
        {p.program_id: p.college_id for p in programs if p.college_id not in known_college_ids}.items()
    )
    if orphaned:
        detail = ", ".join(f"{pid} -> {cid!r}" for pid, cid in orphaned)
        raise ConfigError(
            f"Program(s) reference unknown college_id (not present in {colleges_path}): {detail}"
        )

    return ReferenceData(colleges=colleges, programs=programs)


# Convenience default paths, resolved relative to the repo root regardless of
# the caller's current working directory — avoids "works when I run it from
# the repo root, breaks from anywhere else" bugs.
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COLLEGES_PATH = _REPO_ROOT / "configs" / "colleges.yaml"
DEFAULT_PROGRAMS_PATH = _REPO_ROOT / "configs" / "programs.yaml"


def load_default_reference_data() -> ReferenceData:
    """Load reference data from the project's standard config location."""
    return load_reference_data(DEFAULT_COLLEGES_PATH, DEFAULT_PROGRAMS_PATH)