"""
tests/unit/test_feature_build_result.py

P0.47 regression coverage: FeatureBuildResult is the explicit result
contract build_and_store_ml_features() returns, replacing the previous
untyped dict that orchestration/assets.py's `features` asset was
misreading as a single scalar row count (assigning the whole dict to a
variable named for an int, then passing it into a `records_processed:
int` DuckDB column). These tests need no database -- they only check the
dataclass's shape, which is exactly what was ambiguous before.
"""

from __future__ import annotations

import dataclasses

from pipelines.gold.build_ml_features import FeatureBuildResult


def test_feature_build_result_fields_match_required_contract():
    """P0.47's example contract: program_rows, year_level_rows,
    program_fingerprint, year_level_fingerprint -- all four, no more,
    no fewer, with the specified types."""
    fields = {f.name: f.type for f in dataclasses.fields(FeatureBuildResult)}
    assert fields == {
        "program_rows": "int",
        "year_level_rows": "int",
        "program_fingerprint": "str",
        "year_level_fingerprint": "str",
    }


def test_feature_build_result_total_rows_is_the_sum():
    result = FeatureBuildResult(
        program_rows=120,
        year_level_rows=340,
        program_fingerprint="abc123",
        year_level_fingerprint="def456",
    )
    assert result.total_rows == 460
    assert isinstance(result.total_rows, int)


def test_feature_build_result_is_immutable():
    """frozen=True: a caller (e.g. the Dagster `features` asset) can pass
    this around without worrying a downstream consumer mutates it."""
    result = FeatureBuildResult(
        program_rows=1, year_level_rows=1,
        program_fingerprint="a", year_level_fingerprint="b",
    )
    try:
        result.program_rows = 999  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("FeatureBuildResult should be frozen (immutable)")