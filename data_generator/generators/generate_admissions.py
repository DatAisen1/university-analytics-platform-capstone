"""
data_generator/generators/generate_admissions.py

Derives the admissions funnel (applicants -> accepted -> enrolled) that
P0 #15 requires be checkable, from data that ALREADY EXISTS and is
already correct: student_master.csv's real freshman-enrollment counts
per (cohort_academic_year, college, program).

Why "derive backward" instead of "generate forward": if applicants and
accepted were sampled independently of the real enrolled count, nothing
would guarantee applicants >= accepted >= enrolled -- exactly the kind
of "each dataset generated independently, so relationships between them
are coincidental at best" problem P0 #10/#14 already fixed for
enrollment/graduation/dropout/shifter. Instead:

    enrolled  = the REAL freshman count for that (cohort, college, program)
                (read from student_master.csv -- never re-sampled)
    accepted  = ceil(enrolled / yield_rate)       where yield_rate in
                (0, 1] is sampled per group from admissions_rules.yaml
    applicants = ceil(accepted / acceptance_rate)  where acceptance_rate
                in (0, 1] is sampled per group from admissions_rules.yaml

This guarantees applicants >= accepted >= enrolled for every single row,
by construction, not by chance.

Scope note: this funnel models FRESHMAN admissions only (admission_type
== 'Freshman' in student_master.csv). Transferees don't go through a
first-year applicant funnel in any real institution's sense -- they are
already modeled separately via admission_type_weights in
generate_students.py and are intentionally excluded from this file's
enrolled/accepted/applicants counts.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
import yaml

from pipelines.common.config import ConfigError

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ADMISSIONS_CONFIG_PATH = _REPO_ROOT / "data_generator" / "config" / "admissions_rules.yaml"
DEFAULT_STUDENT_MASTER_PATH = _REPO_ROOT / "data_generator" / "output" / "student_master.csv"
DEFAULT_OUTPUT_DIR = _REPO_ROOT / "data_generator" / "output"

ADMISSIONS_FIELDNAMES: List[str] = [
    "academic_year", "college_id", "program_id", "applicants", "accepted", "enrolled_freshmen",
]


def load_admissions_config(path: Path = DEFAULT_ADMISSIONS_CONFIG_PATH) -> dict:
    """Load and sanity-check data_generator/config/admissions_rules.yaml."""
    if not path.exists():
        raise ConfigError(f"Admissions config not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Malformed YAML in {path}: {exc}") from exc

    required = ["random_seed", "yield_rate_min", "yield_rate_max",
                "acceptance_rate_min", "acceptance_rate_max"]
    for key in required:
        if key not in config:
            raise ConfigError(f"{path} is missing required key: {key!r}")

    for lo_key, hi_key in [("yield_rate_min", "yield_rate_max"),
                            ("acceptance_rate_min", "acceptance_rate_max")]:
        lo, hi = config[lo_key], config[hi_key]
        if not (0.0 < lo <= hi <= 1.0):
            raise ConfigError(
                f"{path}: {lo_key}={lo} and {hi_key}={hi} must satisfy 0 < {lo_key} <= {hi_key} <= 1"
            )

    return config


def derive_funnel_counts(
    enrolled_freshmen: int, yield_rate: float, acceptance_rate: float
) -> Dict[str, int]:
    """Pure function: given a REAL enrolled-freshmen count and two sampled
    rates, derive accepted/applicants such that applicants >= accepted >=
    enrolled_freshmen always holds. Independently unit-testable -- the
    core guarantee this whole module exists to provide.
    """
    if enrolled_freshmen < 0:
        raise ValueError(f"enrolled_freshmen must be >= 0, got {enrolled_freshmen}")
    if not (0.0 < yield_rate <= 1.0):
        raise ValueError(f"yield_rate must be in (0, 1], got {yield_rate}")
    if not (0.0 < acceptance_rate <= 1.0):
        raise ValueError(f"acceptance_rate must be in (0, 1], got {acceptance_rate}")

    accepted = math.ceil(enrolled_freshmen / yield_rate) if enrolled_freshmen > 0 else 0
    applicants = math.ceil(accepted / acceptance_rate) if accepted > 0 else 0
    return {"applicants": applicants, "accepted": accepted, "enrolled_freshmen": enrolled_freshmen}


def generate_all_admissions(
    admissions_config_path: Path = DEFAULT_ADMISSIONS_CONFIG_PATH,
    student_master_path: Path = DEFAULT_STUDENT_MASTER_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> Dict[str, int]:
    """Read the real freshman population from student_master.csv, derive
    the admissions funnel per (cohort_academic_year, college, program),
    and write output/{academic_year}/admissions.csv (one file per
    academic year, matching this generator's existing per-year
    partitioning convention).

    Returns a summary dict: {academic_year: rows_written}.
    """
    if not student_master_path.exists():
        raise ConfigError(
            f"{student_master_path} not found -- run generate_students before generate_admissions "
            f"(the admissions funnel is DERIVED from the real student population, not independent)."
        )

    config = load_admissions_config(admissions_config_path)
    rng = np.random.default_rng(config["random_seed"])

    student_df = pd.read_csv(student_master_path)
    freshmen = student_df[student_df["admission_type"] == "Freshman"]

    grouped = (
        freshmen.groupby(["cohort_academic_year", "entry_college_id", "entry_program_id"])
        .size()
        .reset_index(name="enrolled_freshmen")
    )

    rows_by_year: Dict[str, List[dict]] = {}
    for _, row in grouped.iterrows():
        yield_rate = float(rng.uniform(config["yield_rate_min"], config["yield_rate_max"]))
        acceptance_rate = float(rng.uniform(config["acceptance_rate_min"], config["acceptance_rate_max"]))
        funnel = derive_funnel_counts(int(row["enrolled_freshmen"]), yield_rate, acceptance_rate)

        academic_year = str(row["cohort_academic_year"])
        rows_by_year.setdefault(academic_year, []).append({
            "academic_year": academic_year,
            "college_id": row["entry_college_id"],
            "program_id": row["entry_program_id"],
            **funnel,
        })

    summary: Dict[str, int] = {}
    for academic_year, rows in rows_by_year.items():
        year_dir = output_dir / academic_year
        year_dir.mkdir(parents=True, exist_ok=True)
        with (year_dir / "admissions.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=ADMISSIONS_FIELDNAMES)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row[k] for k in ADMISSIONS_FIELDNAMES})
        summary[academic_year] = len(rows)

    return summary


if __name__ == "__main__":
    result = generate_all_admissions()
    print("Generated admissions funnel (derived from real freshman enrollment):")
    for year, count in sorted(result.items()):
        print(f"  {year}: {count} (college, program) rows")