"""
models/forecasting/model_registry.py

Task 39 (Champion/Candidate/Promote) + Task 40-42 (Model Versioning).

  * `decide_promotion` / `should_retrain` are PURE functions (plain
    values in, plain decision out) -- independently unit-testable
    against hand-picked cases without a database, same reasoning as
    models/forecasting/metrics.py being separate from train_prophet.py.
  * Everything below the "Database access" divider is the only code
    in the project that talks to gold.model_registry -- SQL stays out
    of the decision logic above it.

Promotion criteria (Task 39, both required):
  1. The candidate must beat the best baseline (naive or
     historical-average, whichever is lower) on MAE.
  2. If a champion already exists for this (college, metric), the
     candidate's MAE must be no worse than the champion's. No champion
     yet (first-ever run for a series) trivially satisfies this.

Retraining criteria (Task 42): a series is only retrained when the
currently available data covers a period_ordinal beyond the last
training run's training_data_end_period_ordinal -- i.e. a genuinely
new academic year/semester became available. A changed row count
within periods already trained on (late corrections, backfill) is NOT,
by itself, a retraining trigger -- see should_retrain's docstring.

Every candidate -- promoted or not, retrained or skipped -- that DOES
get trained is recorded in gold.model_registry with full provenance
(Task 40: model_version, algorithm, training_data_start/end,
training_record_count, evaluation_metrics, trained_at, is_champion).
No row's provenance is ever overwritten (Task 41): the only UPDATE
this module issues is is_champion = FALSE on the row being demoted
during a promotion. get_model_history / get_model_for_forecast answer
"which model generated this forecast?" directly from that history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional


@dataclass(frozen=True)
class CandidateMetrics:
    """Walk-forward evaluation result for one (college, metric) candidate.
    Field names intentionally match the columns
    models.forecasting.train_prophet.evaluate_all_series /
    compute_metrics_for_model already produce, so callers can build
    this directly without renaming."""

    mae: float
    rmse: float
    mape: Optional[float]  # None when MAPE is undefined (metrics.mape raised ValueError)
    r2: float
    best_baseline_mae: float
    beats_baseline: bool


@dataclass(frozen=True)
class TrainingMetadata:
    """Task 40's required provenance fields beyond what CandidateMetrics
    covers: which algorithm produced this candidate, and exactly what
    training window/volume it saw. Required (not optional) on every
    call to record_candidate() -- Task 41's "which model generated this
    forecast?" is only answerable if this is captured at training time,
    not reconstructed later.
    """

    algorithm: str
    training_data_start_period_ordinal: int
    training_data_end_period_ordinal: int
    training_record_count: int


@dataclass(frozen=True)
class ChampionRecord:
    """The currently-deployed champion for a (college, metric) series --
    just enough for decide_promotion. For the full row, see ModelRecord
    / get_model_history."""

    model_registry_key: int
    model_version: str
    mae: float
    artifact_path: str


@dataclass(frozen=True)
class ModelRecord:
    """One full row from gold.model_registry -- every column, including
    Task 40's provenance fields. Used by get_model_history and
    get_model_for_forecast (Task 41), where a caller needs the full
    audit trail rather than just the MAE decide_promotion needs."""

    model_registry_key: int
    college_key: int
    metric: str
    model_version: str
    algorithm: Optional[str]
    trained_at: datetime
    training_data_start_period_ordinal: Optional[int]
    training_data_end_period_ordinal: Optional[int]
    training_record_count: Optional[int]
    mae: float
    rmse: float
    mape: Optional[float]
    r2: float
    best_baseline_mae: float
    beats_baseline: bool
    is_champion: bool
    promoted_at: Optional[datetime]
    rejected_reason: Optional[str]
    artifact_path: str


@dataclass(frozen=True)
class PromotionDecision:
    promote: bool
    reason: str


@dataclass(frozen=True)
class RetrainDecision:
    should_retrain: bool
    reason: str


def decide_promotion(candidate: CandidateMetrics, champion: Optional[ChampionRecord]) -> PromotionDecision:
    """Pure promotion rule -- see module docstring for the two criteria."""
    if not candidate.beats_baseline:
        return PromotionDecision(
            promote=False,
            reason=(
                f"candidate MAE {candidate.mae:.4f} does not beat the best baseline "
                f"MAE {candidate.best_baseline_mae:.4f}"
            ),
        )

    if champion is not None and candidate.mae > champion.mae:
        return PromotionDecision(
            promote=False,
            reason=(
                f"candidate MAE {candidate.mae:.4f} is worse than current champion "
                f"{champion.model_version} (MAE {champion.mae:.4f})"
            ),
        )

    if champion is None:
        return PromotionDecision(promote=True, reason="beats baseline; no existing champion (bootstrap)")
    return PromotionDecision(
        promote=True,
        reason=f"beats baseline and improves on champion {champion.model_version} "
        f"(candidate MAE {candidate.mae:.4f} <= champion MAE {champion.mae:.4f})",
    )


def should_retrain(current_max_period_ordinal: int, last_trained_period_ordinal: Optional[int]) -> RetrainDecision:
    """Pure retraining gate (Task 42). Deliberately takes period
    ORDINALS, never a row count -- "how much data do we have now"
    conflates two different things if measured by row count: (a) a
    genuinely new academic period becoming available, which SHOULD
    trigger a retrain, and (b) the SAME periods gaining or losing rows
    from late corrections, backfills, or a program-level grain change,
    which should NOT. Because this function's signature only accepts
    period_ordinal values, it structurally cannot be triggered by (b)
    even by accident -- there's no row-count parameter to misuse.
    """
    if last_trained_period_ordinal is None:
        return RetrainDecision(True, "no previous model exists for this series (bootstrap)")

    if current_max_period_ordinal > last_trained_period_ordinal:
        gained = current_max_period_ordinal - last_trained_period_ordinal
        return RetrainDecision(
            True,
            f"{gained} new academic period(s) available since the last training run "
            f"(last trained through period_ordinal {last_trained_period_ordinal}, "
            f"data now available through period_ordinal {current_max_period_ordinal})",
        )

    if current_max_period_ordinal < last_trained_period_ordinal:
        return RetrainDecision(
            False,
            f"current max period_ordinal ({current_max_period_ordinal}) is BEHIND the last "
            f"training run's training_data_end_period_ordinal ({last_trained_period_ordinal}) "
            "-- data appears to have regressed; not retraining automatically, this needs investigation",
        )

    return RetrainDecision(
        False,
        f"no new academic period since the last training run (still through period_ordinal "
        f"{current_max_period_ordinal}) -- a changed row count alone is not a retraining trigger",
    )


def make_model_version(college_id: str, metric: str, trained_at: Optional[datetime] = None) -> str:
    """Deterministic, sortable, globally-unique model_version string,
    e.g. 'CICT_enrollment_count_20260802T140501Z'."""
    trained_at = trained_at or datetime.now(timezone.utc)
    return f"{college_id}_{metric}_{trained_at.strftime('%Y%m%dT%H%M%SZ')}"


# --------------------------------------