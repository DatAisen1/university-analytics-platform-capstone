"""
tests/unit/test_config.py

Unit tests for pipelines/common/config.py.

Coverage philosophy: we test the loader's *behavior at its boundaries* --
valid input works, and each distinct failure mode (missing file, malformed
YAML, wrong shape, duplicate IDs, orphaned foreign keys) produces a clear,
specific ConfigError -- rather than only testing the happy path. A config
loader's entire job is catching bad input early with a useful message, so
the failure paths are the actual product being tested here, not an
afterthought.
"""

from pathlib import Path

import pytest
import yaml

from pipelines.common.config import (
    ConfigError,
    DEFAULT_COLLEGES_PATH,
    DEFAULT_PROGRAMS_PATH,
    load_colleges,
    load_programs,
    load_reference_data,
)


# ---------------------------------------------------------------------------
# Happy path: the real project configs
# ---------------------------------------------------------------------------

def test_real_colleges_config_loads():
    colleges = load_colleges(DEFAULT_COLLEGES_PATH)
    assert len(colleges) == 8
    assert {c.college_id for c in colleges} >= {"COA", "COED", "COE", "CICT"}


def test_real_programs_config_loads():
    programs = load_programs(DEFAULT_PROGRAMS_PATH)
    assert len(programs) == 37
    assert all(p.nominal_duration_years > 0 for p in programs)


def test_real_reference_data_cross_validates():
    ref = load_reference_data(DEFAULT_COLLEGES_PATH, DEFAULT_PROGRAMS_PATH)
    assert len(ref.colleges) == 8
    assert len(ref.programs) == 37


def test_every_program_maps_to_a_valid_college():
    """This is the Day 3 validation-checklist item, made explicit as a test
    rather than a manual eyeball check."""
    ref = load_reference_data(DEFAULT_COLLEGES_PATH, DEFAULT_PROGRAMS_PATH)
    known_college_ids = {c.college_id for c in ref.colleges}
    for program in ref.programs:
        assert program.college_id in known_college_ids, (
            f"{program.program_id} references unknown college {program.college_id!r}"
        )


def test_lookup_helpers():
    ref = load_reference_data(DEFAULT_COLLEGES_PATH, DEFAULT_PROGRAMS_PATH)
    cict = ref.college_by_id("CICT")
    assert cict.college_name == "College of Information and Communications Technology"

    cict_programs = ref.programs_for_college("CICT")
    assert len(cict_programs) == 4
    assert all(p.college_id == "CICT" for p in cict_programs)

    with pytest.raises(KeyError):
        ref.college_by_id("NOPE")


# ---------------------------------------------------------------------------
# Failure paths: missing file
# ---------------------------------------------------------------------------

def test_missing_file_raises_config_error(tmp_path):
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(ConfigError, match="not found"):
        load_colleges(missing)


# ---------------------------------------------------------------------------
# Failure paths: malformed YAML
# ---------------------------------------------------------------------------

def test_malformed_yaml_raises_clear_error(tmp_path):
    bad_file = tmp_path / "colleges.yaml"
    # Intentionally broken YAML: unbalanced brackets
    bad_file.write_text("version: 1\ncolleges: [unclosed list\n")
    with pytest.raises(ConfigError, match="Malformed YAML"):
        load_colleges(bad_file)


def test_empty_file_raises_clear_error(tmp_path):
    empty_file = tmp_path / "colleges.yaml"
    empty_file.write_text("")
    with pytest.raises(ConfigError, match="empty"):
        load_colleges(empty_file)


def test_non_mapping_yaml_raises_clear_error(tmp_path):
    list_file = tmp_path / "colleges.yaml"
    list_file.write_text("- just\n- a\n- list\n")
    with pytest.raises(ConfigError, match="mapping"):
        load_colleges(list_file)


# ---------------------------------------------------------------------------
# Failure paths: schema (shape) violations
# ---------------------------------------------------------------------------

def test_college_missing_required_field_raises_config_error(tmp_path):
    bad_file = tmp_path / "colleges.yaml"
    bad_file.write_text(
        yaml.dump({"version": 1, "colleges": [{"college_id": "COA"}]})  # missing college_name
    )
    with pytest.raises(ConfigError):
        load_colleges(bad_file)


def test_program_invalid_level_raises_config_error(tmp_path):
    bad_file = tmp_path / "programs.yaml"
    bad_file.write_text(
        yaml.dump(
            {
                "version": 1,
                "programs": [
                    {
                        "program_id": "X-1",
                        "program_name": "Test Program",
                        "college_id": "COA",
                        "program_level": "Postgraduate",  # not a valid enum value
                        "nominal_duration_years": 4,
                    }
                ],
            }
        )
    )
    with pytest.raises(ConfigError):
        load_programs(bad_file)


def test_unknown_field_is_rejected(tmp_path):
    """extra='forbid' means a typo'd key (e.g. 'collage_name') fails loudly
    instead of being silently ignored and leaving college_name unset."""
    bad_file = tmp_path / "colleges.yaml"
    bad_file.write_text(
        yaml.dump(
            {
                "version": 1,
                "colleges": [
                    {"college_id": "COA", "college_name": "College of Architecture", "collage_name": "typo"}
                ],
            }
        )
    )
    with pytest.raises(ConfigError):
        load_colleges(bad_file)


# ---------------------------------------------------------------------------
# Failure paths: cross-reference violations (the ones load_colleges/
# load_programs alone can't catch -- these require load_reference_data)
# ---------------------------------------------------------------------------

def _write(path: Path, data: dict):
    path.write_text(yaml.dump(data))


def test_duplicate_college_id_raises_config_error(tmp_path):
    colleges_path = tmp_path / "colleges.yaml"
    programs_path = tmp_path / "programs.yaml"
    _write(colleges_path, {
        "version": 1,
        "colleges": [
            {"college_id": "COA", "college_name": "College of Architecture"},
            {"college_id": "COA", "college_name": "Duplicate College"},
        ],
    })
    _write(programs_path, {"version": 1, "programs": []})

    with pytest.raises(ConfigError, match="Duplicate college_id"):
        load_reference_data(colleges_path, programs_path)


def test_duplicate_program_id_raises_config_error(tmp_path):
    colleges_path = tmp_path / "colleges.yaml"
    programs_path = tmp_path / "programs.yaml"
    _write(colleges_path, {
        "version": 1,
        "colleges": [{"college_id": "COA", "college_name": "College of Architecture"}],
    })
    _write(programs_path, {
        "version": 1,
        "programs": [
            {"program_id": "COA-X", "program_name": "A", "college_id": "COA",
             "program_level": "Bachelor", "nominal_duration_years": 4},
            {"program_id": "COA-X", "program_name": "B (duplicate id)", "college_id": "COA",
             "program_level": "Bachelor", "nominal_duration_years": 4},
        ],
    })

    with pytest.raises(ConfigError, match="Duplicate program_id"):
        load_reference_data(colleges_path, programs_path)


def test_orphaned_program_college_reference_raises_config_error(tmp_path):
    """The core Day 3 requirement: a program pointing at a college_id that
    doesn't exist must fail loudly, not silently produce a program with no
    valid college."""
    colleges_path = tmp_path / "colleges.yaml"
    programs_path = tmp_path / "programs.yaml"
    _write(colleges_path, {
        "version": 1,
        "colleges": [{"college_id": "COA", "college_name": "College of Architecture"}],
    })
    _write(programs_path, {
        "version": 1,
        "programs": [
            {"program_id": "GHOST-1", "program_name": "Orphan Program", "college_id": "NOPE",
             "program_level": "Bachelor", "nominal_duration_years": 4},
        ],
    })

    with pytest.raises(ConfigError, match="unknown college_id"):
        load_reference_data(colleges_path, programs_path)
