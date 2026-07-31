"""
data_generator/validation/generate_validation_report.py

Produces a pre-ingestion validation report over the data_generator's raw
output (P0 #16) -- run this BEFORE pipelines.ingestion.ingest_to_bronze,
not instead of Bronze/Silver's own checks. It reuses two things that
already exist rather than reinventing validation logic:

  1. pipelines.common.schemas (Bronze pandera shape schemas) -- run
     directly against the generator's raw CSVs.
  2. pipelines.silver.progression_validation (P0 #13's impossible year-
     level transition detector) -- run here, at the source, so a bad
     generator run is caught before it ever reaches Silver.

This module is intentionally tolerant of BOTH of this project's observed
enrollment-file column conventions -- 'semester_name' (current
generate_progression.py output) and 'semester_number' (the convention
found in the currently checked-in data_generator/output/ CSVs, which
predate a since-refactored generator). Silently picking one and crashing
on the other would make this validator useless for exactly the kind of
generator/output drift it exists to catch -- see this module's own
report output for a concrete example of that drift.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import pandera.errors

from pipelines.common.academic_periods import (
    SEMESTER_LABELS,
    SUPER_SENIOR_LABEL,
    YEAR_LEVEL_LABELS,
    academic_year_label,
    year_level_label,
)
from pipelines.common.config import ConfigError, ReferenceData, load_default_reference_data
from pipelines.common.schemas import validate_bronze_dataframe
from pipelines.silver.progression_validation import find_impossible_year_level_transitions
from data_generator.generators.generate_students import DEFAULT_VOLUMES_PATH, load_volumes_config

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = _REPO_ROOT / "data_generator" / "output"

SEMESTER_SCOPED_ENTITIES = ["enrollment", "graduation", "dropout", "shifter"]


def _semester_col(df: pd.DataFrame) -> str:
    """Return whichever of the two observed semester-column conventions
    is present on this dataframe. Raises if neither/both are present --
    an ambiguous or absent semester column is a hard stop, not a warning."""
    has_name = "semester_name" in df.columns
    has_number = "semester_number" in df.columns
    if has_name and has_number:
        raise ConfigError("Enrollment data has BOTH semester_name and semester_number columns -- ambiguous.")
    if has_name:
        return "semester_name"
    if has_number:
        return "semester_number"
    raise ConfigError("Enrollment data has neither semester_name nor semester_number -- cannot validate.")


def _semester_label(value, col_name: str) -> str:
    """Normalize either convention to the canonical '1st Semester'/'2nd
    Semester' label used by SEMESTER_LABELS."""
    if col_name == "semester_name":
        return str(value)
    return "1st Semester" if int(value) == 1 else "2nd Semester"


def _load_entity_files(output_dir: Path, entity: str) -> pd.DataFrame:
    """Concatenate every {entity}.csv found anywhere under output_dir,
    regardless of partitioning convention (numeric-folder or
    semester_name-folder) -- deliberately convention-agnostic, see
    module docstring."""
    frames = [pd.read_csv(p) for p in sorted(output_dir.rglob(f"{entity}.csv"))]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def check_period_coverage(
    enrollment_df: pd.DataFrame, expected_years: List[int]
) -> Tuple[Dict[str, bool], Dict[str, bool]]:
    """Which expected academic_year labels and semester labels actually
    appear at least once in the enrollment data."""
    if enrollment_df.empty:
        return (
            {academic_year_label(y): False for y in expected_years},
            {s: False for s in SEMESTER_LABELS},
        )

    sem_col = _semester_col(enrollment_df)
    present_years = {academic_year_label(y) for y in enrollment_df["academic_year"].unique()}
    present_semesters = {_semester_label(v, sem_col) for v in enrollment_df[sem_col].unique()}

    year_results = {academic_year_label(y): academic_year_label(y) in present_years for y in expected_years}
    semester_results = {s: s in present_semesters for s in SEMESTER_LABELS}
    return year_results, semester_results


def check_year_level_coverage(
    enrollment_df: pd.DataFrame, reference: ReferenceData
) -> Dict[str, bool]:
    """Which of the 5 canonical year_level labels (Freshman .. Super
    Senior) appear at least once, computed program-aware via the P0 #12
    fix -- NOT a hardcoded absolute year_level cutoff."""
    expected_labels = list(YEAR_LEVEL_LABELS.values()) + [SUPER_SENIOR_LABEL]
    if enrollment_df.empty:
        return {label: False for label in expected_labels}

    duration_by_program = {p.program_id: p.nominal_duration_years for p in reference.programs}
    labels_found = set()
    for _, row in enrollment_df.iterrows():
        duration = duration_by_program.get(row["program_id"])
        if duration is None:
            continue  # orphan program_id -- surfaced separately by check_orphan_references
        labels_found.add(year_level_label(row["year_level"], duration))

    return {label: label in labels_found for label in expected_labels}


def check_duplicate_records(enrollment_df: pd.DataFrame) -> int:
    """Count of (student_id, academic_year, semester) combinations that
    appear more than once -- should be 0 given a correct generator; this
    check exists to CONFIRM that, not assume it."""
    if enrollment_df.empty:
        return 0
    sem_col = _semester_col(enrollment_df)
    dupe_mask = enrollment_df.duplicated(subset=["student_id", "academic_year", sem_col], keep=False)
    return int(dupe_mask.sum())


def check_orphan_references(
    entity_dfs: Dict[str, pd.DataFrame], student_df: pd.DataFrame
) -> Dict[str, int]:
    """Count rows in enrollment/graduation/dropout/shifter referencing a
    student_id that doesn't exist in student_master.csv -- should be 0 by
    construction, since the generator writes student_master first."""
    known_ids = set(student_df["student_id"])
    orphan_counts: Dict[str, int] = {}
    for entity, df in entity_dfs.items():
        if df.empty:
            orphan_counts[entity] = 0
            continue
        orphan_counts[entity] = int((~df["student_id"].isin(known_ids)).sum())
    return orphan_counts


def check_schema_violations(entity_dfs: Dict[str, pd.DataFrame], student_df: pd.DataFrame) -> Dict[str, int]:
    """Run each entity's Bronze pandera schema directly against the raw
    generator output (pipelines.common.schemas) -- the same shape checks
    Bronze ingestion runs post-write, but here BEFORE ingestion."""
    violations: Dict[str, int] = {}
    all_dfs = dict(entity_dfs)
    all_dfs["student"] = student_df
    for entity, df in all_dfs.items():
        if df.empty:
            violations[entity] = 0
            continue
        try:
            validate_bronze_dataframe(df, entity)
            violations[entity] = 0
        except pandera.errors.SchemaErrors as exc:
            violations[entity] = len(exc.failure_cases)
    return violations


def build_validation_report(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    volumes_path: Path = DEFAULT_VOLUMES_PATH,
    reference: Optional[ReferenceData] = None,
) -> dict:
    """Run every P0 #16 check and return a structured report dict."""
    reference = reference or load_default_reference_data()
    volumes_config = load_volumes_config(volumes_path)
    expected_years = sorted(int(y) for y in volumes_config["cohort_sizes"].keys())

    student_master_path = output_dir / "student_master.csv"
    if not student_master_path.exists():
        raise ConfigError(f"{student_master_path} not found -- run the generator before validating.")
    student_df = pd.read_csv(student_master_path)

    entity_dfs = {entity: _load_entity_files(output_dir, entity) for entity in SEMESTER_SCOPED_ENTITIES}
    enrollment_df = entity_dfs["enrollment"]

    year_coverage, semester_coverage = check_period_coverage(enrollment_df, expected_years)
    year_level_coverage = check_year_level_coverage(enrollment_df, reference)
    duplicate_count = check_duplicate_records(enrollment_df)
    orphan_counts = check_orphan_references(entity_dfs, student_df)
    schema_violation_counts = check_schema_violations(entity_dfs, student_df)
    transition_violations = find_impossible_year_level_transitions(enrollment_df) if not enrollment_df.empty else pd.DataFrame()

    total_invalid = (
        sum(orphan_counts.values())
        + sum(schema_violation_counts.values())
        + len(transition_violations)
    )

    return {
        "academic_years": year_coverage,
        "semesters": semester_coverage,
        "year_levels": year_level_coverage,
        "duplicate_records": duplicate_count,
        "invalid_records": {
            "orphan_student_references": orphan_counts,
            "schema_violations": schema_violation_counts,
            "impossible_year_level_transitions": len(transition_violations),
            "total": total_invalid,
        },
    }


def format_report(report: dict) -> str:
    """Render the report in the exact ✓/✗ checklist format requested."""
    lines: List[str] = []

    lines.append("Academic years:")
    for year, ok in report["academic_years"].items():
        lines.append(f"{year} {'✓' if ok else '✗'}")

    lines.append("")
    lines.append("Semesters:")
    for sem, ok in report["semesters"].items():
        short = "1st" if sem == "1st Semester" else "2nd"
        lines.append(f"{short} {'✓' if ok else '✗'}")

    lines.append("")
    lines.append("Year levels:")
    for label, ok in report["year_levels"].items():
        lines.append(f"{label} {'✓' if ok else '✗'}")

    lines.append("")
    lines.append("Duplicate records:")
    lines.append(str(report["duplicate_records"]))

    lines.append("")
    lines.append("Invalid records:")
    lines.append(str(report["invalid_records"]["total"]))
    if report["invalid_records"]["total"] > 0:
        lines.append("  breakdown:")
        lines.append(f"    orphan student references: {report['invalid_records']['orphan_student_references']}")
        lines.append(f"    schema violations: {report['invalid_records']['schema_violations']}")
        lines.append(f"    impossible year_level transitions: {report['invalid_records']['impossible_year_level_transitions']}")

    return "\n".join(lines)


def main() -> int:
    """Entry point: build + print + write the report. Returns a process
    exit code (0 = clean, 1 = at least one check failed) so this can gate
    Bronze ingestion in a CI/Makefile step without extra glue code."""
    report = build_validation_report()
    text = format_report(report)
    print(text)

    output_path = DEFAULT_OUTPUT_DIR / "validation_report.txt"
    output_path.write_text(text, encoding="utf-8")

    all_periods_ok = all(report["academic_years"].values()) and all(report["semesters"].values())
    all_year_levels_ok = all(report["year_levels"].values())
    no_invalid = report["invalid_records"]["total"] == 0
    no_duplicates = report["duplicate_records"] == 0

    return 0 if (all_periods_ok and all_year_levels_ok and no_invalid and no_duplicates) else 1


if __name__ == "__main__":
    sys.exit(main())