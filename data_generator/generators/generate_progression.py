"""
data_generator/generators/generate_progression.py

Simulates each student's semester-by-semester journey from their entry
semester through 2024-2 (or until a terminal outcome), producing:
  - enrollment records   (one row per student per semester enrolled)
  - graduation events
  - dropout events
  - shifter events

Written out partitioned by academic_year/semester, matching how a real
registrar export would arrive at Bronze ingestion (Day 8) -- one batch
per semester, not one giant all-history file.

IMPORTANT KNOWN LIMITATION (surfaced here deliberately, not buried):
Every student's simulation STARTS at their cohort's entry semester within
the observed 2021-2024 window. There is no population of students who
already enrolled *before* 2021 and are continuing into the window (e.g.
real 2021-1 seniors who started in 2018). That means:
  - 2021-1's simulated population is 100% brand-new entrants -- no
    continuing 2nd/3rd/4th/5th-year students exist on day one, which a
    real university obviously would have.
  - 4-year programs can only produce a graduate if a student entered in
    the 2021 cohort AND survives to exactly their 8th semester (2024-2) --
    the single last semester in the observed window. 5-year programs
    (Architecture, Engineering) cannot produce ANY natural graduate within
    this window at all, since even the 2021 cohort only reaches 8 semesters
    of tenure by 2024-2, short of the 10 semesters a 5-year program needs.
  - This means observed graduation counts/rates will be far lower, and far
    more concentrated in 2024-2, than the ~1,500-2,500 events estimated in
    docs/08_Faker_Data_Generator.md Section 7 -- that estimate implicitly
    assumed an ongoing institution with students at every year level
    already present in 2021-1, which this generator does not simulate.
See docs/14_Future_Improvements.md for the proposed fix (simulating
"legacy" pre-2021 entry cohorts as unobserved backstory) and why it's
deferred rather than silently patched into the probability model to hit
a target number.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import yaml

from pipelines.common.academic_periods import academic_year_label, academic_year_start_year, semester_label_from_number
from pipelines.common.config import ConfigError, Program, ReferenceData, load_default_reference_data
from data_generator.rules.progression_rules import (
    dropout_probability,
    graduation_probability,
    max_year_level_cap,
    sample_dropout_reason,
    shift_probability,
    stall_probability,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROGRESSION_CONFIG_PATH = _REPO_ROOT / "data_generator" / "config" / "progression_rules.yaml"
DEFAULT_STUDENT_MASTER_PATH = _REPO_ROOT / "data_generator" / "output" / "student_master.csv"
DEFAULT_RISK_PROFILES_PATH = _REPO_ROOT / "data_generator" / "output" / "_internal" / "student_latent_profiles.csv"
DEFAULT_OUTPUT_DIR = _REPO_ROOT / "data_generator" / "output"

ANCHOR_YEAR = 2021  # semester index 0 == (2021-2022, 1st Semester)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_progression_config(path: Path = DEFAULT_PROGRESSION_CONFIG_PATH) -> dict:
    if not path.exists():
        raise ConfigError(f"Progression config not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Malformed YAML in {path}: {exc}") from exc

    required_top_level = ["dropout", "graduation", "stall", "shifter", "enrollment", "max_semester_index"]
    for key in required_top_level:
        if key not in config:
            raise ConfigError(f"{path} is missing required key: {key!r}")

    reason_weights = config["dropout"]["reason_weights"]
    total = sum(reason_weights.values())
    if abs(total - 1.0) > 0.001:
        raise ConfigError(
            f"dropout.reason_weights in {path} must sum to 1.0 (±0.001), got {total:.4f}"
        )

    return config


# ---------------------------------------------------------------------------
# Semester index helpers
# ---------------------------------------------------------------------------

def semester_index_to_label(idx: int) -> tuple[str, str]:
    """0 -> (2021-2022, 1st Semester), 1 -> (2021-2022, 2nd Semester), 2 -> (2022-2023, 1st Semester), ..."""
    academic_year = academic_year_label(ANCHOR_YEAR + idx // 2)
    semester_name = semester_label_from_number(1 if idx % 2 == 0 else 2)
    return academic_year, semester_name


def entry_semester_index(cohort_year: int) -> int:
    """The semester index at which a cohort's students first enroll."""
    return (cohort_year - ANCHOR_YEAR) * 2


# ---------------------------------------------------------------------------
# Program-target selection for shifters
# ---------------------------------------------------------------------------

def pick_shift_target_program(
    rng: np.random.Generator, reference: ReferenceData, current_program: Program, config: dict
) -> Program:
    """Choose a new program for a shifting student: with probability
    `same_college_weight`, pick another program in the same college;
    otherwise pick a program in a different college."""
    same_college_weight = config["shifter"]["same_college_weight"]

    if rng.random() < same_college_weight:
        candidates = [
            p for p in reference.programs_for_college(current_program.college_id)
            if p.program_id != current_program.program_id
        ]
        if not candidates:  # college has only one program -- fall back to any other program
            candidates = [p for p in reference.programs if p.program_id != current_program.program_id]
    else:
        candidates = [p for p in reference.programs if p.college_id != current_program.college_id]

    return candidates[int(rng.integers(0, len(candidates)))]


# ---------------------------------------------------------------------------
# Per-student simulation
# ---------------------------------------------------------------------------

def simulate_student(
    student_id: str,
    cohort_year: int,
    entry_year_level: int,
    entry_program: Program,
    risk_score: float,
    reference: ReferenceData,
    rng: np.random.Generator,
    config: dict,
) -> dict:
    """Simulate one student from their entry semester through
    config['max_semester_index'] (or a terminal outcome, whichever first).

    Returns a dict with keys: enrollment_records (list[dict]),
    graduation_record (dict|None), dropout_record (dict|None),
    shifter_records (list[dict]), final_status (str).
    """
    max_index = config["max_semester_index"]
    units_min = config["enrollment"]["units_enrolled_min"]
    units_max = config["enrollment"]["units_enrolled_max"]

    idx = entry_semester_index(cohort_year)
    year_level = entry_year_level
    current_program = entry_program
    nominal_semesters = round(current_program.nominal_duration_years * 2)
    year_level_cap = max_year_level_cap(current_program.nominal_duration_years, config)

    tenure_semesters = 0
    stall_count = 0
    has_shifted = False

    enrollment_records: List[dict] = []
    graduation_record: Optional[dict] = None
    dropout_record: Optional[dict] = None
    shifter_records: List[dict] = []
    final_status = "ACTIVE"

    while idx <= max_index:
        tenure_semesters += 1
        academic_year, semester_name = semester_index_to_label(idx)
        semester_number = 1 if idx % 2 == 0 else 2

        # 1. Dropout check
        d_prob = dropout_probability(year_level, risk_score, stall_count, config)
        if rng.random() < d_prob:
            reason = sample_dropout_reason(rng, config)
            dropout_record = {
                "student_id": student_id,
                "academic_year": academic_year,
                "semester_name": semester_name,
                "semester_number": semester_number,
                "program_id": current_program.program_id,
                "college_id": current_program.college_id,
                "dropout_reason": reason,
                "semesters_completed_before_dropout": tenure_semesters - 1,
            }
            enrollment_records.append({
                "student_id": student_id, "academic_year": academic_year, "semester_name": semester_name,
                "semester_number": semester_number,
                "college_id": current_program.college_id, "program_id": current_program.program_id,
                "enrollment_status": "DROPPED", "year_level": year_level,
                "units_enrolled": 0, "is_new_enrollee": tenure_semesters == 1,
            })
            final_status = "DROPPED"
            break

        # 2. Graduation check (only once eligible)
        if tenure_semesters >= nominal_semesters:
            g_prob = graduation_probability(tenure_semesters, nominal_semesters, risk_score, config)
            if rng.random() < g_prob:
                graduation_record = {
                    "student_id": student_id,
                    "academic_year": academic_year,
                    "semester_name": semester_name,
                    "semester_number": semester_number,
                    "program_id": current_program.program_id,
                    "college_id": current_program.college_id,
                    "years_to_complete": round(tenure_semesters / 2, 1),
                }
                enrollment_records.append({
                    "student_id": student_id, "academic_year": academic_year, "semester_name": semester_name,
                    "semester_number": semester_number,
                    "college_id": current_program.college_id, "program_id": current_program.program_id,
                    "enrollment_status": "GRADUATED", "year_level": year_level,
                    "units_enrolled": int(rng.integers(units_min, units_max + 1)),
                    "is_new_enrollee": tenure_semesters == 1,
                })
                final_status = "GRADUATED"
                break

        # 3. Shifter check (years 1-2 only, at most once per student)
        if not has_shifted and year_level in (1, 2) and rng.random() < shift_probability(year_level, config):
            new_program = pick_shift_target_program(rng, reference, current_program, config)
            shifter_records.append({
                "student_id": student_id,
                "academic_year": academic_year,
                "semester_name": semester_name,
                "semester_number": semester_number,
                "from_program_id": current_program.program_id,
                "to_program_id": new_program.program_id,
            })
            current_program = new_program
            has_shifted = True
            # nominal_semesters is intentionally NOT recalculated from the new
            # program: a shifter's completion clock is a policy question
            # (credit transfer rules) outside this generator's scope -- see
            # docs/08_Faker_Data_Generator.md limitations.

        # 4. Emit this semester's enrollment record (still enrolled)
        enrollment_records.append({
            "student_id": student_id, "academic_year": academic_year, "semester_name": semester_name,
            "semester_number": semester_number,
            "college_id": current_program.college_id, "program_id": current_program.program_id,
            "enrollment_status": "ENROLLED", "year_level": year_level,
            "units_enrolled": int(rng.integers(units_min, units_max + 1)),
            "is_new_enrollee": tenure_semesters == 1,
        })

        # 5. Year-level progression / stall, evaluated at each year boundary
        if tenure_semesters % 2 == 0:
            if year_level >= year_level_cap or rng.random() < stall_probability(risk_score, config):
                stall_count += 1
            else:
                year_level += 1

        idx += 1

    return {
        "enrollment_records": enrollment_records,
        "graduation_record": graduation_record,
        "dropout_record": dropout_record,
        "shifter_records": shifter_records,
        "final_status": final_status,
    }


# ---------------------------------------------------------------------------
# Full-population orchestration
# ---------------------------------------------------------------------------

def _read_students_and_risk(student_master_path: Path, risk_profiles_path: Path) -> List[dict]:
    with student_master_path.open() as f:
        students = list(csv.DictReader(f))
    with risk_profiles_path.open() as f:
        risk_by_id = {row["student_id"]: float(row["risk_score"]) for row in csv.DictReader(f)}

    missing = [s["student_id"] for s in students if s["student_id"] not in risk_by_id]
    if missing:
        raise ConfigError(
            f"{len(missing)} student(s) in {student_master_path} have no matching risk profile "
            f"in {risk_profiles_path} (e.g. {missing[:5]}) -- were the two files generated together?"
        )

    for s in students:
        s["risk_score"] = risk_by_id[s["student_id"]]
    return students


def generate_all_progression(
    progression_config_path: Path = DEFAULT_PROGRESSION_CONFIG_PATH,
    student_master_path: Path = DEFAULT_STUDENT_MASTER_PATH,
    risk_profiles_path: Path = DEFAULT_RISK_PROFILES_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    reference: Optional[ReferenceData] = None,
) -> Dict[str, int]:
    """Simulate every student in student_master.csv across all observed
    semesters, writing enrollment/graduation/dropout/shifter records
    partitioned by academic_year/semester under output_dir.

    Returns a summary dict: counts of enrollment/graduation/dropout/shifter
    records written, plus a per-cohort reconciliation check result.
    """
    config = load_progression_config(progression_config_path)
    reference = reference or load_default_reference_data()
    program_lookup = reference.as_program_lookup()

    students = _read_students_and_risk(student_master_path, risk_profiles_path)
    rng = np.random.default_rng(config["random_seed"])

    # Buckets keyed by (academic_year, semester_number) -> list of rows
    enrollment_by_partition: Dict[tuple, List[dict]] = {}
    graduation_by_partition: Dict[tuple, List[dict]] = {}
    dropout_by_partition: Dict[tuple, List[dict]] = {}
    shifter_by_partition: Dict[tuple, List[dict]] = {}

    outcome_by_cohort: Dict[str, Dict[str, int]] = {}

    for student in students:
        student_id = student["student_id"]
        cohort_year = academic_year_start_year(student["cohort_academic_year"])
        entry_year_level = int(student["entry_year_level"])
        entry_program = program_lookup[student["entry_program_id"]]
        risk_score = student["risk_score"]

        result = simulate_student(
            student_id, cohort_year, entry_year_level, entry_program,
            risk_score, reference, rng, config,
        )

        for rec in result["enrollment_records"]:
            key = (rec["academic_year"], rec["semester_name"])
            enrollment_by_partition.setdefault(key, []).append(rec)

        if result["graduation_record"]:
            g = result["graduation_record"]
            key = (g["academic_year"], g["semester_name"])
            graduation_by_partition.setdefault(key, []).append(g)

        if result["dropout_record"]:
            d = result["dropout_record"]
            key = (d["academic_year"], d["semester_name"])
            dropout_by_partition.setdefault(key, []).append(d)

        for sh in result["shifter_records"]:
            key = (sh["academic_year"], sh["semester_name"])
            shifter_by_partition.setdefault(key, []).append(sh)

        cohort_key = str(cohort_year)
        outcome_by_cohort.setdefault(cohort_key, {"ACTIVE": 0, "GRADUATED": 0, "DROPPED": 0})
        outcome_by_cohort[cohort_key][result["final_status"]] += 1

    _write_partitions(output_dir, "enrollment", enrollment_by_partition,
                       ["student_id", "academic_year", "semester_number", "college_id", "program_id",
                        "enrollment_status", "year_level", "units_enrolled", "is_new_enrollee"])
    _write_partitions(output_dir, "graduation", graduation_by_partition,
                       ["student_id", "academic_year", "semester_number", "program_id", "college_id",
                        "years_to_complete"])
    _write_partitions(output_dir, "dropout", dropout_by_partition,
                       ["student_id", "academic_year", "semester_number", "program_id", "college_id",
                        "dropout_reason", "semesters_completed_before_dropout"])
    _write_partitions(output_dir, "shifter", shifter_by_partition,
                       ["student_id", "academic_year", "semester_number", "from_program_id", "to_program_id"])

    total_students = len(students)
    reconciled = all(
        sum(counts.values()) == sum(
            1 for s in students if academic_year_start_year(s["cohort_academic_year"]) == int(cohort)
        )
        for cohort, counts in outcome_by_cohort.items()
    )

    return {
        "total_students": total_students,
        "total_enrollment_records": sum(len(v) for v in enrollment_by_partition.values()),
        "total_graduation_events": sum(len(v) for v in graduation_by_partition.values()),
        "total_dropout_events": sum(len(v) for v in dropout_by_partition.values()),
        "total_shifter_events": sum(len(v) for v in shifter_by_partition.values()),
        "outcome_by_cohort": outcome_by_cohort,
        "cohort_totals_reconciled": reconciled,
    }


def _write_partitions(
    output_dir: Path, entity_name: str, records_by_partition: Dict[tuple, List[dict]], fieldnames: List[str]
) -> None:
    for (academic_year, semester_name), rows in records_by_partition.items():
        partition_dir = output_dir / str(academic_year) / str(semester_name)
        partition_dir.mkdir(parents=True, exist_ok=True)
        with (partition_dir / f"{entity_name}.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row[k] for k in fieldnames})


if __name__ == "__main__":
    summary = generate_all_progression()
    print(f"Simulated {summary['total_students']} students:")
    print(f"  enrollment records : {summary['total_enrollment_records']}")
    print(f"  graduation events  : {summary['total_graduation_events']}")
    print(f"  dropout events     : {summary['total_dropout_events']}")
    print(f"  shifter events     : {summary['total_shifter_events']}")
    print(f"  cohort totals reconciled: {summary['cohort_totals_reconciled']}")
    print("  outcome by cohort:")
    for cohort, counts in sorted(summary["outcome_by_cohort"].items()):
        print(f"    {cohort}: {counts}")
