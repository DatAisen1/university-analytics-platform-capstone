"""
tests/integration/test_generate_all_determinism.py

P0.34-P0.37 -- Deterministic Data Generation.

Proves, end-to-end, the acceptance criteria that only per-function unit
tests (test_generate_students.py etc.) couldn't cover on their own:

  * Same seed + configuration + dataset schema version produces an
    equivalent (here: byte-identical) dataset.
  * The dataset can be regenerated from scratch (empty output_dir).
  * The manifest correctly records the seeds/config actually used.
  * Pre-ingestion validation runs and passes on generator output.

Uses the real, default data_generator/config/*.yaml files (the same ones
`python -m data_generator.generators.generate_all` uses) so that "does the
pre-ingestion validator actually PASS" is checked against a realistic
population. A full run is ~3-4s, so running it twice per test is cheap
enough for the regular suite; a synthetic tiny-cohort fixture was
considered but rejected because at very small sample sizes the
progression engine can stochastically fail to produce every year level
(e.g. no student reaches "Super Senior"), which would make validation
pass/fail flaky for reasons unrelated to what this test is checking.
"""

from __future__ import annotations

import filecmp
import json
from pathlib import Path

import yaml

from data_generator.generators.generate_all import (
    DATASET_SCHEMA_VERSION,
    MANIFEST_FILENAME,
    generate_full_dataset,
)
from data_generator.generators.generate_students import DEFAULT_VOLUMES_PATH


def _all_relative_files(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


def test_same_seed_and_config_produce_identical_dataset(tmp_path):
    out_a = tmp_path / "run_a"
    out_b = tmp_path / "run_b"

    manifest_a = generate_full_dataset(output_dir=out_a)
    manifest_b = generate_full_dataset(output_dir=out_b)

    files_a = _all_relative_files(out_a)
    files_b = _all_relative_files(out_b)
    assert files_a == files_b, "regenerated dataset has a different file set than the original run"

    non_manifest_files = files_a - {MANIFEST_FILENAME}
    _, mismatched, errors = filecmp.cmpfiles(
        out_a, out_b, non_manifest_files, shallow=False,
    )
    assert not mismatched, f"non-deterministic output files: {mismatched}"
    assert not errors, f"comparison errors: {errors}"

    # Manifests must agree on everything except the generation timestamp.
    for manifest in (manifest_a, manifest_b):
        assert manifest["dataset_schema_version"] == DATASET_SCHEMA_VERSION
        assert manifest["validation"]["ran"] is True
        assert manifest["validation"]["passed"] is True
    assert manifest_a["seeds"] == manifest_b["seeds"]
    assert manifest_a["config_hashes"] == manifest_b["config_hashes"]
    assert manifest_a["row_counts"] == manifest_b["row_counts"]


def test_dataset_regenerates_from_empty_output_dir(tmp_path):
    output_dir = tmp_path / "from_scratch"
    assert not output_dir.exists()

    manifest = generate_full_dataset(output_dir=output_dir)

    assert output_dir.exists()
    assert (output_dir / "student_master.csv").exists()
    assert (output_dir / MANIFEST_FILENAME).exists()
    assert manifest["validation"]["passed"] is True
    assert sum(manifest["row_counts"]["students_by_cohort"].values()) > 0


def test_clean_flag_wipes_stale_output_before_regenerating(tmp_path):
    output_dir = tmp_path / "stale_then_clean"
    output_dir.mkdir()
    stale_file = output_dir / "leftover_from_a_previous_run.csv"
    stale_file.write_text("student_id\nSTALE-0001\n", encoding="utf-8")

    generate_full_dataset(output_dir=output_dir, clean=True)

    assert not stale_file.exists(), "clean=True must remove pre-existing output before regenerating"


def test_manifest_records_seed_actually_used(tmp_path):
    output_dir = tmp_path / "seeded"
    manifest = generate_full_dataset(output_dir=output_dir)

    on_disk_manifest = json.loads((output_dir / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert on_disk_manifest["seeds"] == manifest["seeds"]
    assert set(manifest["seeds"]) == {
        "generate_students", "generate_admissions", "generate_progression", "apply_noise",
    }


def test_different_seed_produces_different_dataset(tmp_path):
    """Sanity check on the determinism test itself: changing the seed
    must actually change the output, or the "identical" assertion above
    would be trivially true for the wrong reason (e.g. a generator that
    ignores its seed entirely). Uses a small cohort override purely for
    speed -- validation is skipped here since this test only cares
    whether output *differs*, not whether it's valid."""
    config = yaml.safe_load(DEFAULT_VOLUMES_PATH.read_text(encoding="utf-8"))
    config["cohort_sizes"] = {2021: 40, 2022: 45, 2023: 50}

    seed_a_path = tmp_path / "seed_a_volumes.yaml"
    config_a = dict(config)
    config_a["random_seed"] = 42
    seed_a_path.write_text(yaml.dump(config_a), encoding="utf-8")

    seed_b_path = tmp_path / "seed_b_volumes.yaml"
    config_b = dict(config)
    config_b["random_seed"] = 4242
    seed_b_path.write_text(yaml.dump(config_b), encoding="utf-8")

    out_a = tmp_path / "seed_a"
    out_b = tmp_path / "seed_b"
    generate_full_dataset(output_dir=out_a, volumes_path=seed_a_path, validate=False)
    generate_full_dataset(output_dir=out_b, volumes_path=seed_b_path, validate=False)

    assert not filecmp.cmp(
        out_a / "student_master.csv", out_b / "student_master.csv", shallow=False,
    )