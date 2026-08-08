"""
data_generator/generators/generate_all.py

P0.34-P0.37 -- Deterministic Data Generation.

Single authoritative orchestrator for the data_generator package. Every
individual generator (generate_students, generate_admissions,
generate_progression, apply_noise) already draws from an explicit,
independent seed defined in its own config file -- that part of P0.35 was
already true before this module existed. What was missing is the thing
docs/08_Faker_Data_Generator.md already *claimed* existed (a
`generate_all.py` orchestrating generation order): one entry point that

  1. runs every stage, in dependency order, against a single output_dir;
  2. runs the pre-ingestion validator (P0.37) before declaring success;
  3. writes a manifest.json recording exactly which seed/config/dataset
     schema version produced the output_dir's contents, so "same seed +
     configuration + dataset version -> equivalent data" (P0.36) is
     something you can actually check after the fact, not just something
     that happens to be true if you inspect the generator source; and
  4. can regenerate the dataset from an empty output_dir (the acceptance
     criterion "dataset can be regenerated from scratch"), by clearing
     output_dir first unless the caller opts out.

This module intentionally contains no new randomization logic. It only
sequences the four existing generators and records what happened.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from data_generator.generators.apply_noise import (
    DEFAULT_NOISE_CONFIG_PATH,
    apply_all_noise,
    load_noise_config,
)
from data_generator.generators.generate_admissions import (
    DEFAULT_ADMISSIONS_CONFIG_PATH,
    generate_all_admissions,
    load_admissions_config,
)
from data_generator.generators.generate_progression import (
    DEFAULT_PROGRESSION_CONFIG_PATH,
    generate_all_progression,
    load_progression_config,
)
from data_generator.generators.generate_students import (
    DEFAULT_VOLUMES_PATH,
    generate_all_students,
    load_volumes_config,
)
from data_generator.validation.generate_validation_report import (
    build_validation_report,
    format_report,
)
from pipelines.common.config import load_default_reference_data

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = _REPO_ROOT / "data_generator" / "output"

# Bumped whenever the *shape* of the generated dataset changes (new/removed
# columns, new entity files, changed partitioning) -- NOT for every config
# tweak (cohort sizes, weights, noise rates). Two runs with the same
# dataset_schema_version are expected to be structurally comparable even if
# volumes.yaml's counts differ; two runs with the same schema version AND
# the same seeds AND the same config file contents are expected to be
# byte-identical.
DATASET_SCHEMA_VERSION = "1.0.0"

MANIFEST_FILENAME = "manifest.json"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clean_output_dir(output_dir: Path) -> None:
    """Remove any previously generated dataset so this run starts from
    scratch. Never touches anything outside output_dir."""
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def generate_full_dataset(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    volumes_path: Path = DEFAULT_VOLUMES_PATH,
    admissions_config_path: Path = DEFAULT_ADMISSIONS_CONFIG_PATH,
    progression_config_path: Path = DEFAULT_PROGRESSION_CONFIG_PATH,
    noise_config_path: Path = DEFAULT_NOISE_CONFIG_PATH,
    *,
    clean: bool = True,
    apply_noise: bool = True,
    validate: bool = True,
) -> dict:
    """Run the full deterministic generation pipeline into output_dir and
    return the manifest that was also written to
    output_dir/manifest.json.

    Stage order (each stage's output is a hard input to the next):
        generate_students -> generate_admissions -> generate_progression
        -> pre-ingestion validation -> apply_noise

    Validation runs BEFORE noise injection, not after. apply_noise
    deliberately introduces duplicate submissions, typos, and late
    corrections (data_generator/config/noise_rules.yaml) so Silver's
    dedup/cleaning logic has real messiness to prove itself against --
    that is expected noise, not a defect. The pre-ingestion validator's
    "no duplicate records" / "no invalid records" checks exist to catch
    genuine generator bugs (bad foreign keys, missing academic periods,
    impossible year-level transitions), so it must see the clean,
    pre-noise output; running it after apply_noise would make its
    duplicate check permanently and meaninglessly fail by design.

    Raises RuntimeError if `validate=True` and the pre-ingestion validator
    finds any invalid/duplicate records or missing academic periods in
    the pre-noise output -- a bad generator run must not silently look
    like a good one.
    """
    output_dir = Path(output_dir)
    if clean:
        _clean_output_dir(output_dir)
    else:
        output_dir.mkdir(parents=True, exist_ok=True)

    reference = load_default_reference_data()

    volumes_config = load_volumes_config(volumes_path)
    student_summary = generate_all_students(
        volumes_path=volumes_path, output_dir=output_dir, reference=reference,
    )

    admissions_config = load_admissions_config(admissions_config_path)
    admissions_summary = generate_all_admissions(
        admissions_config_path=admissions_config_path,
        student_master_path=output_dir / "student_master.csv",
        output_dir=output_dir,
    )

    progression_config = load_progression_config(progression_config_path)
    progression_summary = generate_all_progression(
        progression_config_path=progression_config_path,
        student_master_path=output_dir / "student_master.csv",
        risk_profiles_path=output_dir / "_internal" / "student_latent_profiles.csv",
        output_dir=output_dir,
        reference=reference,
    )

    # Validate the clean, pre-noise output -- see docstring above for why
    # this must happen before apply_noise, not after.
    validation_report: Optional[dict] = None
    validation_passed: Optional[bool] = None
    if validate:
        validation_report = build_validation_report(output_dir=output_dir)
        validation_passed = (
            all(validation_report["academic_years"].values())
            and all(validation_report["semesters"].values())
            and all(validation_report["year_levels"].values())
            and validation_report["duplicate_records"] == 0
            and validation_report["invalid_records"]["total"] == 0
        )
        (output_dir / "validation_report.txt").write_text(
            format_report(validation_report), encoding="utf-8",
        )

    noise_summary: Optional[dict] = None
    noise_config: Optional[dict] = None
    if apply_noise:
        noise_config = load_noise_config(noise_config_path)
        noise_summary = apply_all_noise(
            noise_config_path=noise_config_path, output_dir=output_dir,
        )

    seeds: Dict[str, int] = {
        "generate_students": volumes_config["random_seed"],
        "generate_admissions": admissions_config["random_seed"],
        "generate_progression": progression_config["random_seed"],
    }
    if noise_config is not None:
        seeds["apply_noise"] = noise_config["random_seed"]

    config_hashes: Dict[str, str] = {
        "volumes.yaml": _sha256_file(Path(volumes_path)),
        "admissions_rules.yaml": _sha256_file(Path(admissions_config_path)),
        "progression_rules.yaml": _sha256_file(Path(progression_config_path)),
    }
    if apply_noise:
        config_hashes["noise_rules.yaml"] = _sha256_file(Path(noise_config_path))

    manifest = {
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seeds": seeds,
        "config_hashes": config_hashes,
        "reference_data": {
            "college_count": len(reference.colleges),
            "program_count": len(reference.programs),
        },
        "row_counts": {
            "students_by_cohort": student_summary,
            "admissions_rows_by_year": admissions_summary,
            "progression": progression_summary,
            "noise": noise_summary,
        },
        "validation": {
            "ran": validate,
            "passed": validation_passed,
            "report": validation_report,
        },
    }

    (output_dir / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8",
    )

    if validate and not validation_passed:
        raise RuntimeError(
            "Pre-ingestion validation FAILED after generation -- see "
            f"{output_dir / 'validation_report.txt'} and {output_dir / MANIFEST_FILENAME}. "
            "Refusing to hand a bad dataset to Bronze ingestion."
        )

    return manifest


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deterministically (re)generate the full synthetic UAP dataset.",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
        help="Where to write the dataset (default: data_generator/output).",
    )
    parser.add_argument(
        "--no-clean", action="store_true",
        help="Do not clear output-dir first (default clears it for a true from-scratch run).",
    )
    parser.add_argument(
        "--skip-noise", action="store_true", help="Skip the noise-injection stage.",
    )
    parser.add_argument(
        "--skip-validation", action="store_true",
        help="Skip pre-ingestion validation (not recommended before Bronze ingestion).",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    try:
        manifest = generate_full_dataset(
            output_dir=args.output_dir,
            clean=not args.no_clean,
            apply_noise=not args.skip_noise,
            validate=not args.skip_validation,
        )
    except RuntimeError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    print(f"Dataset schema version: {manifest['dataset_schema_version']}")
    print(f"Seeds: {manifest['seeds']}")
    print(f"Manifest written to: {Path(args.output_dir) / MANIFEST_FILENAME}")
    if manifest["validation"]["ran"]:
        status = "PASS" if manifest["validation"]["passed"] else "FAIL"
        print(f"Pre-ingestion validation: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())