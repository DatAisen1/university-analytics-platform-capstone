"""
data_generator/generators/generate_students.py

Generates the student master population for cohorts 2021-2025 (P0 Dataset
Extension; was 2021-2023): one row per
student, assigned a college/program, demographic attributes, and (in a
separate, internal-only file) a latent risk score that later drives the
Day 5 progression engine's dropout/graduation/shifter probabilities.

Design principles this module follows (see docs/08_Faker_Data_Generator.md):

1. Reference data (colleges/programs) is never duplicated here -- it's
   loaded from configs/colleges.yaml + configs/programs.yaml via the Day 3
   loader (pipelines.common.config), so there is exactly one place that
   defines "what colleges/programs exist."

2. Every sampling decision is a small, pure function: given an RNG and a
   weight mapping, return a draw. Pure functions are what make "unit test
   the generator's probability functions" (Day 4's testing checklist) an
   actual well-defined task instead of "run the whole generator and eyeball
   the output."

3. The latent risk_score is written to a SEPARATE, clearly-marked internal
   file (output/_internal/student_latent_profiles.csv), never merged into
   student_master.csv. A real registrar export has no "dropout propensity"
   column -- if we baked it into the public file, later pipeline stages
   could accidentally "cheat" by reading a ground-truth label that
   wouldn't exist in reality, which would make the whole capstone's
   Silver/Gold logic unrealistically easy to get right.

Run from the project root as a module, not as a bare script path:
    python -m data_generator.generators.generate_students
(Running it as `python data_generator/generators/generate_students.py`
puts the script's own directory, not the repo root, at the front of
sys.path -- so the `from pipelines.common.config import ...` import below
would fail to find the `pipelines` package. This is the same sys.path
mechanics that motivated renaming this package away from `faker/` in the
first place: know what ends up on sys.path before relying on it.)
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List

import numpy as np
import yaml

from pipelines.common.academic_periods import academic_year_label
from pipelines.common.config import ConfigError, Program, ReferenceData, load_default_reference_data
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VOLUMES_PATH = _REPO_ROOT / "data_generator" / "config" / "volumes.yaml"
DEFAULT_OUTPUT_DIR = _REPO_ROOT / "data_generator" / "output"

NOMINAL_ENTRY_AGE = 18


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_volumes_config(path: Path = DEFAULT_VOLUMES_PATH) -> dict:
    """Load and sanity-check data_generator/config/volumes.yaml.

    Raises ConfigError (the same exception type Day 3 established for all
    config problems) if the file is missing, malformed, or if any weight
    mapping doesn't sum to ~1.0.
    """
    if not path.exists():
        raise ConfigError(f"Volumes config not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Malformed YAML in {path}: {exc}") from exc

    required_weight_keys = [
        "college_weights",
        "admission_type_weights",
        "gender_weights",
        "home_province_weights",
        "age_offset_weights",
    ]
    for key in required_weight_keys:
        if key not in config:
            raise ConfigError(f"{path} is missing required key: {key!r}")
        validate_weights_sum_to_one(config[key], label=key)

    if "program_weights" not in config:
        raise ConfigError(f"{path} is missing required key: 'program_weights'")
    for college_id, weights in config["program_weights"].items():
        validate_weights_sum_to_one(weights, label=f"program_weights[{college_id!r}]")

    if "cohort_sizes" not in config or not config["cohort_sizes"]:
        raise ConfigError(f"{path} must define at least one entry under 'cohort_sizes'")

    if "risk_profile" not in config:
        raise ConfigError(f"{path} is missing required key: 'risk_profile'")
    for param in ("beta_alpha", "beta_beta"):
        if param not in config["risk_profile"]:
            raise ConfigError(f"{path}'s risk_profile is missing required key: {param!r}")

    return config


def validate_weights_sum_to_one(weights: Dict[str, float], label: str, tolerance: float = 0.001) -> None:
    """A weight mapping that doesn't sum to ~1.0 is a config authoring bug
    (a typo'd number, a forgotten entry) -- catch it here rather than
    silently producing a skewed distribution nobody asked for."""
    total = sum(weights.values())
    if abs(total - 1.0) > tolerance:
        raise ConfigError(
            f"Weights under {label!r} must sum to 1.0 (±{tolerance}), got {total:.4f}: {weights}"
        )


def validate_college_weights_match_reference(
    college_weights: Dict[str, float], reference: ReferenceData
) -> None:
    """Cross-reference check: every college_id used as a weight key must
    actually exist in configs/colleges.yaml. This is the same class of
    check Day 3 built for programs -> colleges; applied here to volumes
    config -> colleges."""
    known_ids = {c.college_id for c in reference.colleges}
    unknown = set(college_weights) - known_ids
    if unknown:
        raise ConfigError(
            f"college_weights references unknown college_id(s) not present in "
            f"configs/colleges.yaml: {sorted(unknown)}"
        )
    missing = known_ids - set(college_weights)
    if missing:
        raise ConfigError(
            f"college_weights is missing weight(s) for known college_id(s): {sorted(missing)}"
        )


def validate_program_weights_match_reference(
    program_weights: Dict[str, Dict[str, float]], reference: ReferenceData
) -> None:
    """Cross-reference check, one level deeper than college_weights: every
    college_id key in program_weights must exist, AND the set of
    program_id keys under it must EXACTLY match that college's programs
    in configs/programs.yaml -- not a subset, not a superset. A mismatch
    here (a renamed/added/removed program in programs.yaml that
    volumes.yaml wasn't updated for) is a config authoring bug that
    should fail loudly at generation time, not silently under- or
    over-weight a program that no longer (or doesn't yet) exist.
    """
    known_college_ids = {c.college_id for c in reference.colleges}
    unknown_colleges = set(program_weights) - known_college_ids
    if unknown_colleges:
        raise ConfigError(
            f"program_weights references unknown college_id(s): {sorted(unknown_colleges)}"
        )
    missing_colleges = known_college_ids - set(program_weights)
    if missing_colleges:
        raise ConfigError(
            f"program_weights is missing entries for known college_id(s): {sorted(missing_colleges)}"
        )

    for college_id, weights in program_weights.items():
        expected_program_ids = {p.program_id for p in reference.programs_for_college(college_id)}
        actual_program_ids = set(weights)
        if expected_program_ids != actual_program_ids:
            missing_programs = expected_program_ids - actual_program_ids
            extra_programs = actual_program_ids - expected_program_ids
            raise ConfigError(
                f"program_weights[{college_id!r}] program set does not match "
                f"configs/programs.yaml: missing={sorted(missing_programs)}, "
                f"extra={sorted(extra_programs)}"
            )


# ---------------------------------------------------------------------------
# Pure sampling functions -- each one is independently unit-testable
# ---------------------------------------------------------------------------

def normalize_weights(weights: Dict[str, float]) -> Dict[str, float]:
    """Return a copy of `weights` rescaled to sum exactly to 1.0.

    Used defensively even after validate_weights_sum_to_one has already
    checked the total is close to 1.0 -- floating point rounding in the
    YAML source (e.g. 0.333333) can leave a residual that would otherwise
    make numpy's `p` argument reject the array with 'probabilities do not
    sum to 1'.
    """
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()}


def weighted_choice(rng: np.random.Generator, weights: Dict[str, float]) -> str:
    """Draw one key from `weights`, with probability proportional to its value."""
    normalized = normalize_weights(weights)
    keys = list(normalized.keys())
    probs = list(normalized.values())
    return str(rng.choice(keys, p=probs))


def sample_age_offset(rng: np.random.Generator, age_offset_weights: Dict[int, float]) -> int:
    normalized = normalize_weights({str(k): v for k, v in age_offset_weights.items()})
    keys = [int(k) for k in normalized.keys()]
    probs = list(normalized.values())
    return int(rng.choice(keys, p=probs))


def sample_risk_score(rng: np.random.Generator, alpha: float, beta: float) -> float:
    """Latent dropout/stall propensity in [0, 1]. Beta(alpha, beta) with
    alpha < beta skews the population toward low risk, matching the
    real-world shape of student attrition risk (most students are fine;
    a minority carry meaningfully elevated risk)."""
    return float(rng.beta(alpha, beta))

def sample_program(
    rng: np.random.Generator,
    college_id: str,
    program_weights: Dict[str, Dict[str, float]],
    reference: ReferenceData,
) -> "Program":  # noqa: F821 -- Program imported below, quoted to avoid a circular-import-looking hint
    """Weighted program draw WITHIN a college, replacing the previous
    uniform `rng.integers(0, len(college_programs))` pick. Uniform choice
    made every program in a college roughly equally popular (e.g. CICT's
    4 programs each landed within 1-2 points of 25%), which is not how
    real program popularity works -- see docs/15_Student_Lifecycle_Rules.md-
    adjacent realism notes."""
    weights_for_college = program_weights[college_id]
    chosen_program_id = weighted_choice(rng, weights_for_college)
    return reference.as_program_lookup()[chosen_program_id]

def sample_entry_year_level(rng: np.random.Generator, admission_type: str) -> int:
    """Freshmen always enter at year level 1. Transferees enter further
    along -- modeled here as mostly year 2, sometimes year 3, reflecting
    typical credit-transfer patterns."""
    if admission_type == "Freshman":
        return 1
    # Transferee
    return int(rng.choice([2, 3], p=[0.8, 0.2]))


def make_student_id(cohort_year: int, sequence: int) -> str:
    """`{cohort_year}-{sequence:05d}`, e.g. '2021-00001'. Globally unique
    across all cohorts because cohort_year differs; sequence itself only
    needs to be unique within a cohort."""
    return f"{cohort_year}-{sequence:05d}"


# ---------------------------------------------------------------------------
# Cohort generation
# ---------------------------------------------------------------------------

def generate_cohort(
    cohort_year: int,
    size: int,
    reference: ReferenceData,
    config: dict,
    rng: np.random.Generator,
    starting_sequence: int = 1,
) -> List[dict]:
    """Generate `size` student rows for a single entering cohort. Returns a
    list of dicts containing BOTH public (student_master.csv) and internal
    (risk_score) fields -- callers are responsible for splitting them
    before writing, which keeps this function simple to test (one row =
    one dict, easy to assert against) while the file-layout decision lives
    at the write boundary, not here.
    """
    college_weights = config["college_weights"]
    admission_weights = config["admission_type_weights"]
    gender_weights = config["gender_weights"]
    province_weights = config["home_province_weights"]
    age_offset_weights = config["age_offset_weights"]
    risk_alpha = config["risk_profile"]["beta_alpha"]
    risk_beta = config["risk_profile"]["beta_beta"]

    rows = []
    for i in range(size):
        sequence = starting_sequence + i
        student_id = make_student_id(cohort_year, sequence)

        college_id = weighted_choice(rng, college_weights)
        program = sample_program(rng, college_id, config["program_weights"], reference)
        admission_type = weighted_choice(rng, admission_weights)
        gender = weighted_choice(rng, gender_weights)
        home_province = weighted_choice(rng, province_weights)
        age_offset = sample_age_offset(rng, age_offset_weights)
        birth_year = cohort_year - (NOMINAL_ENTRY_AGE + age_offset)
        entry_year_level = sample_entry_year_level(rng, admission_type)
        risk_score = sample_risk_score(rng, risk_alpha, risk_beta)

        rows.append({
            "student_id": student_id,
            "cohort_academic_year": academic_year_label(cohort_year),
            "gender": gender,
            "birth_year": birth_year,
            "home_province": home_province,
            "admission_type": admission_type,
            "entry_year_level": entry_year_level,
            "entry_college_id": college_id,
            "entry_program_id": program.program_id,
            "risk_score": risk_score,  # split out before writing student_master.csv
        })
    return rows


def generate_all_students(
    volumes_path: Path = DEFAULT_VOLUMES_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    reference: ReferenceData | None = None,
) -> Dict[str, int]:
    """Generate the full 2021-2025 student master population and write:
      - output/student_master.csv                       (public, SIS-shaped)
      - output/_internal/student_latent_profiles.csv     (generator-internal only)

    Returns a summary dict of {cohort_year: count} for validation/logging.
    """
    config = load_volumes_config(volumes_path)
    reference = reference or load_default_reference_data()
    validate_college_weights_match_reference(config["college_weights"], reference)
    validate_program_weights_match_reference(config["program_weights"], reference)

    rng = np.random.default_rng(config["random_seed"])

    all_rows: List[dict] = []
    summary: Dict[str, int] = {}
    for cohort_year, size in sorted(config["cohort_sizes"].items()):
        cohort_rows = generate_cohort(int(cohort_year), int(size), reference, config, rng)
        all_rows.extend(cohort_rows)
        summary[str(cohort_year)] = len(cohort_rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    internal_dir = output_dir / "_internal"
    internal_dir.mkdir(parents=True, exist_ok=True)

    public_fields = [
        "student_id", "cohort_academic_year", "gender", "birth_year",
        "home_province", "admission_type", "entry_year_level",
        "entry_college_id", "entry_program_id",
    ]
    with (output_dir / "student_master.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=public_fields)
        writer.writeheader()
        for row in all_rows:
            writer.writerow({k: row[k] for k in public_fields})

    with (internal_dir / "student_latent_profiles.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["student_id", "risk_score"])
        writer.writeheader()
        for row in all_rows:
            writer.writerow({"student_id": row["student_id"], "risk_score": row["risk_score"]})

    return summary


if __name__ == "__main__":
    result = generate_all_students()
    total = sum(result.values())
    print(f"Generated {total} students across {len(result)} cohorts:")
    for year, count in result.items():
        print(f"  {year}: {count}")