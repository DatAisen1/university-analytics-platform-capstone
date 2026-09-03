"""
scripts/kpi_weight_sensitivity.py

P2.4 (KPI Redesign): "How much does the Institutional Success Index's
ranking of colleges depend on the specific weight vector we chose?"

docs/09_Data_Science.md §3 discloses that the weights are a documented
POLICY judgment call, not an empirically derived formula -- a
university's leadership might legitimately weight graduation or
retention higher depending on strategic priorities. This script makes
that disclosure concrete: it reruns the exact same composite formula
under four weight vectors against every already-computed
(college, academic_period) row, and reports where a different policy
choice would have changed which college looks best.

Critically, this does NOT re-derive the formula -- it imports and
calls `compute_success_rate` from pipelines/gold/build_kpi.py directly,
using that function's `weights` override parameter (added in the P2
Redesign specifically so this script could exist). That is what
guarantees the sensitivity analysis can never silently drift from the
production formula: if build_kpi.py's formula shape ever changes, this
script changes with it for free, because it isn't a second copy of the
formula -- it's the same one.

What this script does NOT do:
  - It does not recompute any component (retention_rate, graduation_rate,
    etc.) -- those are read as-is from the already-built
    gold/fact_institution_kpi Parquet output. Only the WEIGHTING of
    those components is varied here.
  - It does not judge which weight vector is "right." That's a policy
    conversation for the institution, per docs/09_Data_Science.md §5 --
    this script's job is only to show what's AT STAKE in that choice.

Usage:
    python -m scripts.kpi_weight_sensitivity
    python -m scripts.kpi_weight_sensitivity --top-n 3
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path
from typing import Dict

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from pipelines.common.storage import ObjectStorage, load_storage_from_env  # noqa: E402
from pipelines.gold.build_kpi import (  # noqa: E402
    DEFAULT_GOLD_STORAGE_PATH,
    WEIGHTS as CURRENT_WEIGHTS,
    compute_success_rate,
)

# Four weight vectors to compare against the production ("current")
# vector. Each is a complete, sum-to-1.0 vector over the same six
# component keys `compute_success_rate` expects -- not a delta on top
# of CURRENT_WEIGHTS, so each one stands on its own as a coherent
# policy choice, the same way CURRENT_WEIGHTS does.
EQUAL_WEIGHTS: Dict[str, float] = {
    "graduation_rate": 1 / 6,
    "retention_rate": 1 / 6,
    "dropout_rate": 1 / 6,
    "program_completion_momentum": 1 / 6,
    "shifter_stability": 1 / 6,
    "enrollment_volatility": 1 / 6,
}

# Mirrors CURRENT_WEIGHTS' graduation/retention split (0.30/0.25) but
# pushed further in each direction, redistributing the difference
# proportionally across the remaining four components rather than
# zeroing any of them out -- a "graduation matters even more than we
# currently say" policy, not a "nothing else matters" one.
GRADUATION_HEAVY_WEIGHTS: Dict[str, float] = {
    "graduation_rate": 0.50,
    "retention_rate": 0.15,
    "dropout_rate": 0.15,
    "program_completion_momentum": 0.10,
    "shifter_stability": 0.05,
    "enrollment_volatility": 0.05,
}

RETENTION_HEAVY_WEIGHTS: Dict[str, float] = {
    "graduation_rate": 0.15,
    "retention_rate": 0.50,
    "dropout_rate": 0.15,
    "program_completion_momentum": 0.10,
    "shifter_stability": 0.05,
    "enrollment_volatility": 0.05,
}

WEIGHT_VECTORS: Dict[str, Dict[str, float]] = {
    "current": CURRENT_WEIGHTS,
    "equal": EQUAL_WEIGHTS,
    "graduation_heavy": GRADUATION_HEAVY_WEIGHTS,
    "retention_heavy": RETENTION_HEAVY_WEIGHTS,
}


def _read_parquet(storage: ObjectStorage, key: str) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(storage.read_bytes(key)))


def _score_under_vector(kpi: pd.DataFrame, vector_name: str, weights: Dict[str, float]) -> pd.Series:
    """Recompute the composite for every row under one weight vector.

    Reuses `compute_success_rate` as-is -- same function, same rounding
    -- with only `weights` swapped out, so a difference in the output
    can only come from the weighting, never from a formula discrepancy.
    """
    return kpi.apply(
        lambda row: compute_success_rate(
            retention_rate=row["retention_rate"],
            graduation_rate=row["graduation_rate"],
            dropout_rate=row["dropout_rate"],
            shifter_stability=row["shifter_stability"],
            enrollment_volatility=row["enrollment_volatility"],
            program_completion_momentum=row["program_completion_momentum"],
            weights=weights,
        ),
        axis=1,
    ).rename(f"score_{vector_name}")


def build_sensitivity_report(
    kpi: pd.DataFrame, dim_college: pd.DataFrame, dim_academic_period: pd.DataFrame, top_n: int
) -> pd.DataFrame:
    """One row per (college, academic_period, weight_vector): the
    recomputed score plus that college's RANK among colleges in the
    same period under that vector (1 = highest score). Rank, not raw
    score, is what "does the weighting choice change the decision"
    actually means -- a 2-point score wobble is noise, a college
    dropping out of the top N is a decision-relevant flip.
    """
    scored = kpi[["college_key", "academic_period_key"]].copy()
    for vector_name, weights in WEIGHT_VECTORS.items():
        scored[f"score_{vector_name}"] = _score_under_vector(kpi, vector_name, weights)

    for vector_name in WEIGHT_VECTORS:
        scored[f"rank_{vector_name}"] = scored.groupby("academic_period_key")[
            f"score_{vector_name}"
        ].rank(ascending=False, method="min").astype(int)

    scored = scored.merge(dim_college[["college_key", "college_id", "college_name"]], on="college_key")
    scored = scored.merge(
        dim_academic_period[["academic_period_key", "period_label", "period_ordinal"]],
        on="academic_period_key",
    )

    for vector_name in WEIGHT_VECTORS:
        if vector_name == "current":
            continue
        scored[f"rank_flip_vs_current_{vector_name}"] = scored["rank_current"] != scored[f"rank_{vector_name}"]
        scored[f"top{top_n}_flip_vs_current_{vector_name}"] = (
            scored["rank_current"] <= top_n
        ) != (scored[f"rank_{vector_name}"] <= top_n)

    return scored.sort_values(["period_ordinal", "rank_current"]).reset_index(drop=True)


def summarize(report: pd.DataFrame, top_n: int) -> None:
    print(f"Institutional Success Index -- weight sensitivity across {len(WEIGHT_VECTORS)} vectors")
    print(f"Rows: {len(report)} (college x academic_period)\n")

    for vector_name in WEIGHT_VECTORS:
        if vector_name == "current":
            continue
        flip_col = f"rank_flip_vs_current_{vector_name}"
        top_flip_col = f"top{top_n}_flip_vs_current_{vector_name}"
        n_flips = int(report[flip_col].sum())
        n_top_flips = int(report[top_flip_col].sum())
        print(
            f"[{vector_name}] any-rank changes: {n_flips}/{len(report)} rows "
            f"| top-{top_n} membership changes: {n_top_flips}/{len(report)} rows"
        )
        if n_top_flips:
            movers = report.loc[
                report[top_flip_col],
                ["period_label", "college_id", "rank_current", f"rank_{vector_name}"],
            ]
            print(movers.rename(columns={f"rank_{vector_name}": f"rank_{vector_name}"}).to_string(index=False))
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--top-n", type=int, default=3,
        help="Report a rank-N flip as decision-relevant if it crosses this threshold (default: 3).",
    )
    args = parser.parse_args()

    storage = load_storage_from_env(DEFAULT_GOLD_STORAGE_PATH, "MINIO_GOLD_BUCKET")
    kpi = _read_parquet(storage, "gold/fact_institution_kpi/data.parquet")
    dim_college = _read_parquet(storage, "gold/dim_college/data.parquet")
    dim_academic_period = _read_parquet(storage, "gold/dim_academic_period/data.parquet")

    report = build_sensitivity_report(kpi, dim_college, dim_academic_period, args.top_n)
    summarize(report, args.top_n)


if __name__ == "__main__":
    main()