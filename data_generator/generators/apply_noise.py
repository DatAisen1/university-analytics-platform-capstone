"""
data_generator/generators/apply_noise.py

Applies realistic messiness to the already-generated (Day 4 + Day 5)
output files, in place:
  - student_master.csv: typo noise on home_province
  - each {academic_year}/{semester}/enrollment.csv: status-text casing
    noise, in-partition duplicates, and late corrections re-emitted into
    a later partition

This runs as a distinct, final stage (see docs/08_Faker_Data_Generator.md
Section 3's generation-order diagram) -- generation and progression
produce a clean, internally-consistent dataset first; noise is layered on
top afterward, never interleaved with the business-logic simulation. That
separation is what makes it possible to unit test dropout/graduation
probabilities against clean data (Day 5's tests) independently of testing
noise rates (this day's tests).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import yaml

from pipelines.common.config import ConfigError
from data_generator.rules.noise_injection import (
    apply_status_casing_noise,
    introduce_typo,
    should_duplicate,
    should_late_correct,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_NOISE_CONFIG_PATH = _REPO_ROOT / "data_generator" / "config" / "noise_rules.yaml"
DEFAULT_OUTPUT_DIR = _REPO_ROOT / "data_generator" / "output"


def load_noise_config(path: Path = DEFAULT_NOISE_CONFIG_PATH) -> dict:
    if not path.exists():
        raise ConfigError(f"Noise config not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Malformed YAML in {path}: {exc}") from exc

    for key in ("typo_rate", "duplicate_rate", "late_correction_rate", "status_variants"):
        if key not in config:
            raise ConfigError(f"{path} is missing required key: {key!r}")

    for status, variants in config["status_variants"].items():
        total = sum(variants.values())
        if abs(total - 1.0) > 0.001:
            raise ConfigError(
                f"status_variants[{status!r}] in {path} must sum to 1.0 (±0.001), got {total:.4f}"
            )
    return config


# ---------------------------------------------------------------------------
# student_master.csv noise
# ---------------------------------------------------------------------------

def apply_noise_to_student_master(path: Path, config: dict, rng: np.random.Generator) -> int:
    """Apply typo noise to home_province in place. Returns the count of
    rows actually mutated (for reporting/validation)."""
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    mutated = 0
    for row in rows:
        original = row["home_province"]
        noisy = introduce_typo(rng, original, config["typo_rate"])
        if noisy != original:
            mutated += 1
        row["home_province"] = noisy

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return mutated


# ---------------------------------------------------------------------------
# Enrollment partition noise
# ---------------------------------------------------------------------------

def _discover_enrollment_partitions(output_dir: Path) -> List[Tuple[int, int, Path]]:
    """Find every {academic_year}/{semester}/enrollment.csv under
    output_dir, sorted chronologically."""
    partitions = []
    for year_dir in output_dir.iterdir():
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for sem_dir in year_dir.iterdir():
            if not sem_dir.is_dir() or not sem_dir.name.isdigit():
                continue
            enrollment_file = sem_dir / "enrollment.csv"
            if enrollment_file.exists():
                partitions.append((int(year_dir.name), int(sem_dir.name), enrollment_file))
    partitions.sort(key=lambda p: (p[0], p[1]))
    return partitions


def apply_noise_to_enrollment_partitions(
    output_dir: Path, config: dict, rng: np.random.Generator
) -> Dict[str, int]:
    """Apply status-casing noise, in-partition duplicates, and late
    corrections across all enrollment partitions under output_dir, in
    place. Returns observed counts for validation against configured rates.
    """
    partitions = _discover_enrollment_partitions(output_dir)
    if not partitions:
        raise ConfigError(f"No enrollment partitions found under {output_dir} -- run Day 5's generator first")

    partition_rows: Dict[Tuple[int, int], List[dict]] = {}
    fieldnames_by_partition: Dict[Tuple[int, int], List[str]] = {}
    for year, sem, path in partitions:
        with path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames_by_partition[(year, sem)] = reader.fieldnames
            partition_rows[(year, sem)] = list(reader)

    partition_keys = [(y, s) for y, s, _ in partitions]
    total_rows = 0
    status_noised = 0
    duplicated = 0
    late_corrected = 0

    extra_rows_same_partition: Dict[Tuple[int, int], List[dict]] = {k: [] for k in partition_keys}
    late_injections: Dict[Tuple[int, int], List[dict]] = {k: [] for k in partition_keys}

    for i, key in enumerate(partition_keys):
        rows = partition_rows[key]
        later_keys = partition_keys[i + 1:]

        for row in rows:
            total_rows += 1
            true_status = row["enrollment_status"]
            noisy_status = apply_status_casing_noise(rng, true_status, config)
            if noisy_status != true_status:
                status_noised += 1
            row["enrollment_status"] = noisy_status

            if should_duplicate(rng, config):
                extra_rows_same_partition[key].append(dict(row))
                duplicated += 1

            if later_keys and should_late_correct(rng, config):
                target_key = later_keys[int(rng.integers(0, len(later_keys)))]
                late_injections[target_key].append(dict(row))
                late_corrected += 1

    for key in partition_keys:
        combined = partition_rows[key] + extra_rows_same_partition[key] + late_injections[key]
        _, _, path = next(p for p in partitions if (p[0], p[1]) == key)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames_by_partition[key])
            writer.writeheader()
            writer.writerows(combined)

    return {
        "total_rows_processed": total_rows,
        "status_noised": status_noised,
        "duplicated": duplicated,
        "late_corrected": late_corrected,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def apply_all_noise(
    noise_config_path: Path = DEFAULT_NOISE_CONFIG_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> dict:
    config = load_noise_config(noise_config_path)
    rng = np.random.default_rng(config["random_seed"])

    student_master_path = output_dir / "student_master.csv"
    typo_count = apply_noise_to_student_master(student_master_path, config, rng)

    enrollment_summary = apply_noise_to_enrollment_partitions(output_dir, config, rng)

    return {
        "typos_introduced": typo_count,
        **enrollment_summary,
    }


if __name__ == "__main__":
    summary = apply_all_noise()
    print("Noise injection complete:")
    for key, value in summary.items():
        print(f"  {key}: {value}")
