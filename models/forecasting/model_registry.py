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

P1 Graduation_count reporting honesty -- Option B (registrable baseline
champions), added below the original divider:

  Task 39's decide_promotion (above) treats Prophet as the only thing
  that can ever be "the candidate" -- a baseline is purely the bar it
  has to clear. That's a real coverage gap for a low-count metric like
  graduation_count: a tie or a loss to baseline leaves the series with
  NO deployed forecast at all, even though a naive/seasonal forecast
  was sitting right there the whole time, unused.

  select_champion_algorithm() removes that asymmetry: every algorithm
  actually evaluated this cycle (prophet, naive, historical_avg,
  seasonal_naive -- whichever have a defined walk-forward MAE) competes
  on equal footing, lowest MAE wins, ties go to the simpler algorithm
  (ALGORITHM_SIMPLICITY_RANK). decide_champion_promotion() then applies
  only Task 39's SECOND criterion (no worse than the existing champion)
  to that cycle's winner -- the "beats a baseline" criterion is no
  longer a separate gate because the winner is, by construction, at
  least as good as every baseline evaluated this cycle (it might BE
  one). Net effect: a series only goes without a deployed champion when
  it lacks enough history to evaluate anything at all
  (has_sufficient_history in train_prophet.py), not because Prophet in
  particular came up short.

  algorithm remained a free-text, unconstrained VARCHAR(32) column since
  migration 0009 added it ("Tracked explicitly rather than assumed, so
  the registry stays correct the day a second algorithm is introduced" --
  that migration's own comment). No schema change is needed for Option
  B: the day has arrived, the column was already ready for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple


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

    dataset_fingerprint (P1, Forecast Output Contract): the
    pipelines.gold.build_ml_features.feature_dataset_fingerprint()
    value for the exact gold.ml_program_forecast_features snapshot this
    candidate was trained against. Required for the same reason the
    other three fields are -- reconstructing "which dataset version"
    after the fact isn't possible once the table has moved on.
    """

    algorithm: str
    dataset_fingerprint: str
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
    program_key: int
    college_key: Optional[int]
    metric: str
    model_version: str
    algorithm: Optional[str]
    dataset_fingerprint: Optional[str]
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
    # P1.24: baseline metric, Prophet (candidate) metric, and their
    # difference as structured fields -- not just prose buried in
    # `reason` -- so a caller (report, dashboard, audit query) can
    # consume the comparison without parsing a sentence.
    # mae_diff = candidate_mae - baseline_mae: negative means the
    # candidate beat the baseline by that many MAE units, positive
    # means it lost by that much. Defaulted to 0.0 (not left required)
    # so existing call sites that construct a PromotionDecision
    # directly for fixtures/mocking (e.g. tests/unit/test_deploy_forecast.py)
    # don't break -- decide_promotion() itself always fills these in
    # explicitly on every branch, defaults are only a fallback.
    baseline_mae: float = 0.0
    candidate_mae: float = 0.0
    mae_diff: float = 0.0


@dataclass(frozen=True)
class RetrainDecision:
    should_retrain: bool
    reason: str


def get_last_trained_period_ordinal(engine, program_key: int, metric: str) -> Optional[int]:
    """Return the last training window end for a series, if the registry is available."""
    if engine is None:
        return None
    try:
        conn = engine.raw_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT training_data_end_period_ordinal
                FROM gold.model_registry
                WHERE program_key = %s AND metric = %s
                ORDER BY trained_at DESC, model_registry_key DESC
                LIMIT 1
                """,
                (program_key, metric),
            )
            row = cur.fetchone()
        conn.close()
    except Exception:
        return None
    return None if row is None or row[0] is None else int(row[0])


def get_current_champion(engine, program_key: int, metric: str) -> Optional[ChampionRecord]:
    """Return the current champion row for a series, if one exists."""
    if engine is None:
        return None
    try:
        conn = engine.raw_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT model_registry_key, model_version, mae, artifact_path
                FROM gold.model_registry
                WHERE program_key = %s AND metric = %s AND is_champion IS TRUE
                ORDER BY trained_at DESC, model_registry_key DESC
                LIMIT 1
                """,
                (program_key, metric),
            )
            row = cur.fetchone()
        conn.close()
    except Exception:
        return None
    if row is None:
        return None
    return ChampionRecord(
        model_registry_key=int(row[0]),
        model_version=row[1],
        mae=float(row[2]),
        artifact_path=row[3],
    )


def record_candidate(
    engine,
    program_key: int,
    metric: str,
    model_version: str,
    candidate: CandidateMetrics,
    training_meta: TrainingMetadata,
    artifact_path: str,
    decision: PromotionDecision,
    college_key: Optional[int] = None,
) -> int:
    """Insert a candidate row into gold.model_registry and return its key.

    college_key is the denormalized, informational convenience column
    (see migration 0013) -- program_key is the actual grain key used for
    every WHERE clause in this module."""
    if engine is None:
        return 0
    try:
        conn = engine.raw_connection()
        try:
            with conn.cursor() as cur:
                if decision.promote:
                    cur.execute(
                        """
                        UPDATE gold.model_registry
                        SET is_champion = FALSE
                        WHERE program_key = %s AND metric = %s AND is_champion IS TRUE
                        """,
                        (program_key, metric),
                    )
                cur.execute(
                    """
                    INSERT INTO gold.model_registry (
                        program_key, college_key, metric, model_version, mae, rmse, mape, r2,
                        best_baseline_mae, beats_baseline, is_champion, rejected_reason,
                        artifact_path, algorithm, dataset_fingerprint, training_data_start_period_ordinal,
                        training_data_end_period_ordinal, training_record_count
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING model_registry_key
                    """,
                    (
                        program_key,
                        college_key,
                        metric,
                        model_version,
                        candidate.mae,
                        candidate.rmse,
                        candidate.mape,
                        candidate.r2,
                        candidate.best_baseline_mae,
                        candidate.beats_baseline,
                        decision.promote,
                        None if decision.promote else decision.reason,
                        artifact_path,
                        training_meta.algorithm,
                        training_meta.dataset_fingerprint,
                        training_meta.training_data_start_period_ordinal,
                        training_meta.training_data_end_period_ordinal,
                        training_meta.training_record_count,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    except Exception:
        return 0
    return int(row[0]) if row is not None else 0


def decide_promotion(candidate: CandidateMetrics, champion: Optional[ChampionRecord]) -> PromotionDecision:
    """Pure promotion rule -- see module docstring for the two criteria.

    P1.24: every return path fills baseline_mae/candidate_mae/mae_diff,
    so the baseline-vs-candidate comparison is always available as
    structured data, regardless of which branch decided the outcome.
    """
    mae_diff = candidate.mae - candidate.best_baseline_mae

    if not candidate.beats_baseline:
        return PromotionDecision(
            promote=False,
            reason=(
                f"candidate MAE {candidate.mae:.4f} does not beat the best baseline "
                f"MAE {candidate.best_baseline_mae:.4f} (diff {mae_diff:+.4f})"
            ),
            baseline_mae=candidate.best_baseline_mae,
            candidate_mae=candidate.mae,
            mae_diff=mae_diff,
        )

    if champion is not None and candidate.mae > champion.mae:
        return PromotionDecision(
            promote=False,
            reason=(
                f"candidate MAE {candidate.mae:.4f} is worse than current champion "
                f"{champion.model_version} (MAE {champion.mae:.4f}); "
                f"still beat baseline MAE {candidate.best_baseline_mae:.4f} (diff {mae_diff:+.4f})"
            ),
            baseline_mae=candidate.best_baseline_mae,
            candidate_mae=candidate.mae,
            mae_diff=mae_diff,
        )

    if champion is None:
        return PromotionDecision(
            promote=True,
            reason=(
                f"beats baseline MAE {candidate.best_baseline_mae:.4f} with candidate MAE "
                f"{candidate.mae:.4f} (diff {mae_diff:+.4f}); no existing champion (bootstrap)"
            ),
            baseline_mae=candidate.best_baseline_mae,
            candidate_mae=candidate.mae,
            mae_diff=mae_diff,
        )
    return PromotionDecision(
        promote=True,
        reason=(
            f"beats baseline MAE {candidate.best_baseline_mae:.4f} with candidate MAE "
            f"{candidate.mae:.4f} (diff {mae_diff:+.4f}) and improves on champion "
            f"{champion.model_version} (MAE {champion.mae:.4f})"
        ),
        baseline_mae=candidate.best_baseline_mae,
        candidate_mae=candidate.mae,
        mae_diff=mae_diff,
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


def make_model_version(program_id: str, metric: str, algorithm: str, trained_at: Optional[datetime] = None) -> str:
    """Deterministic, sortable, globally-unique model_version string,
    e.g. 'BSCS_graduation_count_seasonal_naive_20260802T140501Z'.

    P1 fix: keyed by program_id, not college_id -- the grain moved from
    (college, metric) to (program, metric); see migration 0013.

    Option B fix: algorithm is now a REQUIRED third component of the
    string, not an afterthought -- uq_model_registry_program_metric_version
    is UNIQUE on (program_key, metric, model_version), and a run that
    evaluates multiple algorithms for the same series needs each one's
    model_version to stay distinguishable even when they're trained in
    the same second."""
    trained_at = trained_at or datetime.now(timezone.utc)
    return f"{program_id}_{metric}_{algorithm}_{trained_at.strftime('%Y%m%dT%H%M%SZ')}"


# --- Option B: multi-algorithm champion selection ---------------------------

@dataclass(frozen=True)
class AlgorithmResult:
    """One algorithm's walk-forward evaluation result for a single series --
    the unit select_champion_algorithm compares. Every algorithm this
    project can register (prophet + the three baselines in baselines.py)
    is represented identically here, so champion selection treats all of
    them uniformly instead of hardcoding Prophet as "the candidate" and
    everything else as "the bar it has to clear" (that hardcoding is
    exactly what CandidateMetrics/decide_promotion above still do, kept
    for backward compatibility -- see this module's Option B docstring
    section)."""

    algorithm: str
    mae: float
    rmse: float
    mape: Optional[float]
    r2: float


# Occam's-razor tie-break order when two algorithms' MAE is equal to
# within floating-point precision -- lower rank wins. Ordered by how much
# machinery each needs to produce its prediction: naive needs one stored
# value, seasonal_naive needs one prior-season value, historical_avg needs
# the full training mean, prophet fits a full model. Not in
# baselines.BASELINE_ALGORITHMS order because that tuple is unordered by
# design; this ranking is a deliberate, separate decision.
ALGORITHM_SIMPLICITY_RANK = {
    "naive": 0,
    "seasonal_naive": 1,
    "historical_avg": 2,
    "prophet": 3,
}


@dataclass(frozen=True)
class ChampionSelection:
    """select_champion_algorithm's result: the winner, why it won, and the
    full ranked field (best-to-worst) so a caller building an audit trail
    or a coverage report doesn't have to re-sort candidates itself."""

    winner: AlgorithmResult
    reason: str
    ranked: Tuple[AlgorithmResult, ...]


def select_champion_algorithm(candidates: Sequence[AlgorithmResult]) -> ChampionSelection:
    """Option B's core decision: out of EVERY algorithm actually evaluated
    this cycle for a series, which one wins? Prophet is not privileged --
    it wins only when it genuinely has the lowest walk-forward MAE among
    whatever was evaluated. Ties (MAE equal within 1e-6) go to the
    simpler algorithm per ALGORITHM_SIMPLICITY_RANK.

    Callers are responsible for excluding any algorithm with an undefined
    MAE before calling this (e.g. seasonal_naive with zero eligible
    walk-forward folds produces NaN -- see train_prophet.walk_forward_evaluate) --
    same division of responsibility evaluate_all_series already uses when
    folding seasonal_naive into best_baseline_mae. A NaN MAE here would
    otherwise silently sort as neither greatest nor least and corrupt
    the ranking.
    """
    if not candidates:
        raise ValueError("select_champion_algorithm requires at least one candidate")

    def sort_key(c: AlgorithmResult) -> Tuple[float, int]:
        return (round(c.mae, 6), ALGORITHM_SIMPLICITY_RANK.get(c.algorithm, 99))

    ranked = tuple(sorted(candidates, key=sort_key))
    winner = ranked[0]
    others = ranked[1:]
    if others:
        detail = "; ".join(f"{c.algorithm} MAE {c.mae:.4f}" for c in others)
        reason = f"{winner.algorithm} wins this cycle with MAE {winner.mae:.4f}, ahead of {detail}"
    else:
        reason = f"{winner.algorithm} is the only algorithm evaluated this cycle (MAE {winner.mae:.4f})"
    return ChampionSelection(winner=winner, reason=reason, ranked=ranked)


def decide_champion_promotion(winner: AlgorithmResult, champion: Optional[ChampionRecord]) -> PromotionDecision:
    """Option B's promotion gate -- supersedes decide_promotion as the
    function models/forecasting/deploy_forecast.py actually calls.

    Task 39's original criterion 1 ("must beat the best baseline") is not
    reproduced here as a separate check: `winner` already came out of
    select_champion_algorithm, so by construction it is at least as good
    as every baseline evaluated this cycle -- possibly because it IS one.
    What's left is exactly Task 39's criterion 2: a series with no
    existing champion promotes trivially (bootstrap); otherwise this
    cycle's winner must be no worse than the champion already deployed
    for this series, regardless of which algorithm produced either one.

    Reuses PromotionDecision (not a new dataclass) so record_candidate
    and every existing caller of that shape keep working unchanged --
    baseline_mae/candidate_mae/mae_diff here describe "current champion
    MAE vs. this cycle's winner MAE" rather than "baseline vs.
    candidate," which is the meaningful comparison left once criterion 1
    is structurally satisfied.
    """
    if champion is None:
        return PromotionDecision(
            promote=True,
            reason=(
                f"{winner.algorithm} selected as this cycle's best-performing algorithm "
                f"(MAE {winner.mae:.4f}); no existing champion (bootstrap)"
            ),
            baseline_mae=winner.mae,
            candidate_mae=winner.mae,
            mae_diff=0.0,
        )

    mae_diff = winner.mae - champion.mae
    if winner.mae > champion.mae:
        return PromotionDecision(
            promote=False,
            reason=(
                f"{winner.algorithm} (MAE {winner.mae:.4f}) is worse than current champion "
                f"{champion.model_version} (MAE {champion.mae:.4f}, diff {mae_diff:+.4f})"
            ),
            baseline_mae=champion.mae,
            candidate_mae=winner.mae,
            mae_diff=mae_diff,
        )

    return PromotionDecision(
        promote=True,
        reason=(
            f"{winner.algorithm} (MAE {winner.mae:.4f}) matches or improves on current champion "
            f"{champion.model_version} (MAE {champion.mae:.4f}, diff {mae_diff:+.4f})"
        ),
        baseline_mae=champion.mae,
        candidate_mae=winner.mae,
        mae_diff=mae_diff,
    )


# --------------------------------------