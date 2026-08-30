"""
models/forecasting/train_prophet.py

Trains a Prophet model per (program, target metric), evaluates it via
walk-forward validation against the exact 3 folds docs/10_Forecasting.md
Section 5 specifies for the canonical 6-semester (2021-2022 through
2023-2024) dataset horizon, compares it against the two required
baselines (naive, historical-average), and refits a final model on the
full history for each series (used by Day 21 to actually forecast the
next semester).

P1 (Data Science Recovery) fix: series now come from
gold.ml_program_forecast_features (pipelines/gold/build_ml_features.py,
Task 31-33's dedicated, leakage-safe, fingerprinted forecast dataset --
grain (college, program, academic_period)), not from a hand-rolled query
against gold.fact_institution_kpi. The KPI fact table is COLLEGE grain
by deliberate design (see build_kpi.py's module docstring) and was never
meant to be the forecasting contract; the feature table already existed
for exactly this purpose but had no consumer until this fix. This also
moves the forecast grain from (college, metric) to (program, metric) --
see migrations/versions/0013_forecast_program_grain.py for the schema
side of this change.

Only 3 walk-forward folds are available under the 6-period grain (down
from 4 under a previous, incorrect 8-semester draft) -- a direct,
disclosed consequence of the academic-calendar fix (docs/10_Forecasting.md
Section 5). With this few folds, per-fold metrics are a point estimate
with wide, disclosed uncertainty, not a precise accuracy figure.

Walk-forward folds (docs/10_Forecasting.md, period_ordinal terms --
period_ordinal is 0-based; see pipelines/gold/build_dimensions.py) for
the CURRENT 6-period observed history -- derive_test_period_ordinals()
derives
these from the dataset's actual max period_ordinal (P1.14 fix), so this
table shifts automatically as OBSERVED_ACADEMIC_YEARS grows; it is not
a hardcoded literal anymore:
  Fold 1: train period_ordinal 0-2, test period_ordinal 3 (2022-2)
  Fold 2: train period_ordinal 0-3, test period_ordinal 4 (2023-1)
  Fold 3: train period_ordinal 0-4, test period_ordinal 5 (2023-2)

Each fold trains ONLY on data strictly before its test point -- the
walk-forward discipline docs/10_Forecasting.md requires specifically
because standard k-fold CV would shuffle future information into
training for a time series, producing artificially optimistic accuracy.

MIN_HISTORY_PERIODS guard: moving to program grain means many more,
smaller series than college grain (a program can have far fewer students
per semester than a whole college). A series with fewer distinct
periods than Fold 1 requires (period_ordinal 0-2 train, 3 test) has no
usable fold at all -- Prophet.fit on 0-1 rows raises, and computing
metrics over an empty actual/predicted list produces silent NaNs that
would otherwise corrupt decide_promotion's comparisons. Such series are
skipped and reported, not silently dropped or allowed to crash the run.

Semester-to-date mapping: semester_number 1 -> Jan 1 of academic_year,
semester_number 2 -> Jul 1 -- the same convention dim_calendar (Day 12)
already established (Jan-Jun = semester 1, Jul-Dec = semester 2 of the
same calendar year), so Prophet's date axis is consistent with the rest
of the project's calendar semantics rather than an unrelated invention.
"""

from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
from pipelines.common.errors import ModelEvaluationError, ModelTrainingError
from models.forecasting.baselines import (
    historical_average_baseline,
    naive_baseline,
    seasonal_naive_baseline,
)
from models.forecasting.count_model import fit_and_predict_count_model
from models.forecasting.metrics import (
    interval_coverage, mae, mape, mean_interval_width, normalized_interval_width, r_squared, rmse,
)

logging.getLogger("cmdstanpy").setLevel(logging.WARNING)
logging.getLogger("prophet").setLevel(logging.WARNING)

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACTS_DIR = _REPO_ROOT / "forecasting" / "artifacts"

TARGET_METRICS = ["enrollment_count", "graduation_count"]

# Fold 1 needs period_ordinal 0-2 (train) + 3 (test) present -- 4 distinct
# periods is the minimum for even one usable fold. Below this, a series
# is skipped rather than fed to Prophet/metrics (see module docstring).
MIN_HISTORY_PERIODS = 4

# 2 semesters/academic year -- see semester_to_date. Used by
# seasonal_naive_baseline to find "the same semester one year prior."
SEASON_LENGTH = 2


def derive_test_period_ordinals(max_period_ordinal: int) -> List[int]:
    """P1.14 fix: the 3 walk-forward test points, DERIVED from the
    highest period_ordinal actually present in the observed data,
    instead of the literal [3, 4, 5] this used to be hardcoded to.

    Named derive_ (not test_) deliberately: pytest collects any callable
    named test_* that lands in a test module's namespace as a test item
    -- and tests/unit/test_train_prophet.py imports this function by
    name, so a bare `test_period_ordinals` was collected as a phantom
    test requiring a nonexistent `max_period_ordinal` fixture,
    erroring at collection. Caught by running the real suite (this
    isn't reachable from unit-testing this function in isolation).

    That literal was only ever correct because OBSERVED_ACADEMIC_YEARS
    (pipelines/common/silver_schemas.py) happened to produce exactly 6
    periods (ordinals 0-5) when it was written -- nothing connected the
    two, so the day observed history grows (a new academic year added),
    the literal would silently stop matching the fold table in
    docs/10_Forecasting.md Section 5 without any test catching it.

    Always returns the last 3 distinct ordinals up to and including
    max_period_ordinal, matching the existing fold table's shape
    (3 folds, each testing one held-out semester strictly after its
    training data) for any dataset horizon >= 4 periods.
    """
    return [max_period_ordinal - 2, max_period_ordinal - 1, max_period_ordinal]


def semester_to_date(academic_year: int, semester_number: int) -> str:
    month_day = "01-01" if semester_number == 1 else "07-01"
    return f"{academic_year}-{month_day}"


def to_prophet_frame(series: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Convert a (program, period) series into the exact ds/y frame
    Prophet.fit/predict requires (P1.10-P1.13).

    Centralizes what used to be three independent, unvalidated
    `.rename(columns={metric: "y"})` call sites (here, in
    train_final_models, and in deploy_forecast.py) -- each renamed the
    column but none of them validated it, so a null metric value, a
    non-numeric dtype, or an out-of-order/duplicated period could reach
    Prophet.fit silently and fail there with an opaque Stan error
    instead of a clear, attributable one at the actual contract boundary.

    Validates (raising ModelTrainingError -- the standard PipelineError
    category every other training failure uses, not a bare
    ValueError/AssertionError):
      ds: convertible to datetime, non-null, unique per series
      y:  present, numeric, non-null, non-negative (both target metrics
          are COUNT(*) aggregates -- a negative count is a contract
          violation, not a valid forecast input)

    Returns a NEW frame (['ds', 'y']) sorted by ds -- callers no longer
    need their own `.sort_values(...)` before fitting.
    """
    if "ds" not in series.columns:
        raise ModelTrainingError(
            "Cannot build Prophet frame: 'ds' column missing from series",
            stage="Prophet Adapter", entity=metric,
        )
    if metric not in series.columns:
        raise ModelTrainingError(
            f"Cannot build Prophet frame: column '{metric}' not present in series",
            stage="Prophet Adapter", entity=metric,
        )

    frame = series[["ds", metric]].rename(columns={metric: "y"}).copy()

    try:
        frame["ds"] = pd.to_datetime(frame["ds"], errors="raise")
    except (ValueError, TypeError) as exc:
        raise ModelTrainingError(
            f"Prophet frame 'ds' column is not convertible to datetime: {exc}",
            stage="Prophet Adapter", entity=metric,
        ) from exc

    if frame["ds"].isna().any():
        raise ModelTrainingError(
            "Prophet frame has null ds value(s) after datetime conversion",
            stage="Prophet Adapter", entity=metric,
            rows_affected=int(frame["ds"].isna().sum()),
        )
    if not pd.api.types.is_numeric_dtype(frame["y"]):
        raise ModelTrainingError(
            f"Prophet frame 'y' column for metric '{metric}' is not numeric "
            f"(dtype={frame['y'].dtype})",
            stage="Prophet Adapter", entity=metric,
        )
    if frame["y"].isna().any():
        raise ModelTrainingError(
            f"Prophet frame has null y value(s) for metric '{metric}'",
            stage="Prophet Adapter", entity=metric,
            rows_affected=int(frame["y"].isna().sum()),
        )
    if (frame["y"] < 0).any():
        raise ModelTrainingError(
            f"Prophet frame 'y' column for metric '{metric}' has negative "
            f"value(s) -- counts cannot be negative",
            stage="Prophet Adapter", entity=metric,
            rows_affected=int((frame["y"] < 0).sum()),
        )
    if frame["ds"].duplicated().any():
        raise ModelTrainingError(
            "Prophet frame has duplicate ds values -- exactly one row per "
            "period is required",
            stage="Prophet Adapter", entity=metric,
            rows_affected=int(frame["ds"].duplicated().sum()),
        )

    return frame.sort_values("ds").reset_index(drop=True)


def load_series(engine) -> pd.DataFrame:
    """One row per (program, period): program_key, program_id,
    college_key, college_id, period_ordinal, ds, and each target
    metric's actual value.

    P1 fix: reads gold.ml_program_forecast_features -- the dedicated,
    leakage-safe forecast dataset (pipelines/gold/build_ml_features.py) --
    instead of querying gold.fact_institution_kpi (college grain)
    directly. Only the raw target columns are selected here; the
    feature table's own lag/rolling/trend columns are not used as
    Prophet regressors (Prophet's univariate ds/y fit is unchanged) --
    consuming them is a separate, future enhancement, not required to
    fix the "arbitrary warehouse table" contract violation this task
    addresses.

    Ordered by period_ordinal, not the academic_period_key surrogate,
    for the same chronological-ordering reason documented in
    build_kpi.py and build_ml_features.py.
    """
    sql = f"""
        SELECT
            mf.program_key, p.program_id, mf.college_key, col.college_id,
            mf.period_ordinal, mf.academic_year, mf.semester_number,
            {', '.join(f'mf.{m}' for m in TARGET_METRICS)}
        FROM gold.ml_program_forecast_features mf
        JOIN gold.dim_program p ON mf.program_key = p.program_key
        JOIN gold.dim_college col ON mf.college_key = col.college_key
        ORDER BY mf.program_key, mf.period_ordinal
    """
    df = pd.read_sql(sql, engine)
    if df.empty:
        # pandas' df.apply(func, axis=1) on a 0-row frame can't infer the
        # lambda's return type and returns an empty DataFrame instead of
        # an empty Series -- df["ds"] = <DataFrame> then raises
        # ValueError: Cannot set a DataFrame with multiple columns to the
        # single column ds. A fresh database with the pipeline not yet
        # run produces exactly this (0 rows), so this must be handled
        # explicitly rather than relying on apply's row-count-dependent
        # return type.
        df["ds"] = pd.Series(dtype="object")
        return df
    df["ds"] = df.apply(lambda r: semester_to_date(int(r["academic_year"]), int(r["semester_number"])), axis=1)
    return df


def has_sufficient_history(series: pd.DataFrame) -> bool:
    """True iff `series` (already filtered to one program) has enough
    distinct periods for at least one walk-forward fold. See
    MIN_HISTORY_PERIODS docstring above."""
    return series["period_ordinal"].nunique() >= MIN_HISTORY_PERIODS


# P0-A.1 fix: yearly_seasonality's intended low Fourier order (2), and
# weekly/daily seasonality's intended disabling, were never actually
# passed to Prophet() -- see fit_prophet's docstring below for why a
# bare Prophet() silently did NOT reliably produce this behavior even
# though it happened to look right on this project's semester-grain
# data most of the time.
YEARLY_SEASONALITY_FOURIER_ORDER = 2


def fit_prophet(train_df: pd.DataFrame):
    """Fit a Prophet model on a training series. yearly_seasonality is
    enabled with a LOW fourier_order (2, vs. Prophet's default 10) --
    with as few as 3 semesters (1.5 years) of training data in the
    earliest fold, a high-order Fourier fit would overfit the tiny
    amount of seasonal signal available. Weekly/daily seasonality are
    disabled outright: this is semester-grain data, and fitting
    sub-semester seasonality to it is fitting noise by definition.

    P0-A.1 fix (previously a real, undetected bug): a bare `Prophet()`
    leaves yearly/weekly/daily seasonality on Prophet's own `'auto'`
    heuristic (`Prophet.set_auto_seasonalities`), NOT on the fixed
    order-2/disabled/disabled configuration this docstring -- and
    every project doc -- described. Concretely, on this project's real
    semester-grain series, 'auto' does not produce the intended
    behavior consistently:
      - yearly: 'auto' disables yearly seasonality entirely for any
        training window under 2 years (the earliest walk-forward
        folds, which have as little as 1.5 years of history -- exactly
        the case this docstring's overfitting warning is about), then
        silently jumps to Prophet's DEFAULT fourier_order=10 (not 2)
        the moment a series' training window crosses 2 years (later
        folds, and the final full-history refit) -- the opposite of a
        stable, intentional order-2 setting, and the highest-order
        overfitting risk exactly where the most training data exists
        to make it look deceptively well-fit.
      - weekly/daily: 'auto' happens to disable both correctly on this
        project's real data today, because consecutive observations
        are always >=1 week apart (semester spacing). This is
        incidental to the data's actual spacing, not a deliberate
        configuration -- nothing here would stop weekly/daily
        seasonality from silently re-enabling if a future data source
        ever had sub-weekly spacing. Disabling both explicitly removes
        the dependency on that coincidence.
    Explicitly passing yearly_seasonality=YEARLY_SEASONALITY_FOURIER_ORDER
    (Prophet accepts a literal Fourier-order int here, not just
    True/False/'auto' -- see Prophet.parse_seasonality_args) makes the
    order-2 configuration this project has always documented actually
    apply on every fold and the final refit, not just on training
    windows that happen to fall on the 'auto' heuristic's disabled
    side. See tests/unit/test_train_prophet.py::
    test_fit_prophet_passes_explicit_seasonality_config_to_prophet for
    the regression test that would have caught the original gap.
    """
    from prophet import Prophet
    try:
        model = Prophet(
            yearly_seasonality=YEARLY_SEASONALITY_FOURIER_ORDER,
            weekly_seasonality=False,
            daily_seasonality=False,
        )
        model.fit(train_df)
        return model
    except Exception as exc:
        raise ModelTrainingError(
            f"Prophet training failed: {exc}", stage="Model Training",
            rows_affected=len(train_df),
        ) from exc


def predict_point(model, ds: str) -> float:
    future = pd.DataFrame({"ds": [ds]})
    forecast = model.predict(future)
    return float(forecast["yhat"].iloc[0])


def predict_interval(model, ds: str) -> Tuple[float, float, float]:
    """(yhat, yhat_lower, yhat_upper) -- Prophet's own fitted 80%
    interval (its default interval_width, unchanged by the P0-A.1
    seasonality fix, which only touched yearly/weekly/daily_seasonality).
    Used by walk_forward_evaluate to compute interval coverage/width
    (P0 Full Re-run gate), not just point MAE/RMSE -- a model can have a
    good point MAE while its stated uncertainty is badly miscalibrated,
    and the old report had no way to show that."""
    future = pd.DataFrame({"ds": [ds]})
    forecast = model.predict(future)
    row = forecast.iloc[0]
    return float(row["yhat"]), float(row["yhat_lower"]), float(row["yhat_upper"])


def load_model(artifact_path: Path):
    """Load a pickled model previously written by one of this package's
    save sites (train_final_models below, models.forecasting.deploy_forecast
    .deploy_forecasts). The counterpart to every `pickle.dump(model, f)`
    call in this project -- without it, gold.model_registry.artifact_path
    recorded a path nothing ever read back, and "can a trained model
    actually be retrieved" (P2.2, MLOps Simplification) was unverified.

    Raises FileNotFoundError with a clearer message than the raw
    pickle/OS error if the artifact is missing, e.g. artifacts_dir wasn't
    persisted or mounted where the caller expected.
    """
    artifact_path = Path(artifact_path)
    if not artifact_path.exists():
        raise FileNotFoundError(f"Model artifact not found at {artifact_path}")
    with artifact_path.open("rb") as f:
        return pickle.load(f)


def walk_forward_evaluate(
    entity_series: pd.DataFrame, metric: str, test_ordinals: Optional[List[int]] = None
) -> Dict[str, Dict[str, List[float]]]:
    """Run all 3 walk-forward folds for one (program, metric) series.
    Grain-agnostic by construction (only reads period_ordinal/ds/metric
    columns) -- callers decide what `entity_series` is filtered to.
    Returns {model_name: {"actual": [...], "predicted": [...]}} with one
    entry per fold, for prophet/naive/historical_avg.

    P1.14 fix: `test_ordinals` is now a parameter, derived (via
    derive_test_period_ordinals()) from the dataset's actual max period_ordinal
    rather than the module-level [3, 4, 5] literal this used to read.
    Callers evaluating many series (evaluate_all_series) should compute
    this ONCE from the full dataset and pass it explicitly, so every
    series is scored against the same fold boundaries -- the default
    (derive from this series alone) exists only for callers evaluating
    a single series in isolation, e.g. in tests.
    """
    series = entity_series.sort_values("period_ordinal").reset_index(drop=True)
    if test_ordinals is None:
        test_ordinals = derive_test_period_ordinals(int(series["period_ordinal"].max()))

    results: Dict[str, Dict[str, List[float]]] = {
        "prophet": {"actual": [], "predicted": [], "lower": [], "upper": []},
        "naive": {"actual": [], "predicted": [], "lower": [], "upper": []},
        "historical_avg": {"actual": [], "predicted": [], "lower": [], "upper": []},
        "seasonal_naive": {"actual": [], "predicted": [], "lower": [], "upper": []},
        # P2.1: count_model (Poisson/Negative-Binomial GLM, see
        # models/forecasting/count_model.py) is only ever fit for
        # graduation_count -- the ONE metric P1.6's measurement showed a
        # real, diagnosed problem for (enrollment_count already beats
        # baseline 78% of the time; there is nothing to fix there). A
        # series with metric != "graduation_count" leaves this bucket
        # empty, which produces a NaN MAE downstream -- the same
        # "no eligible folds" exclusion path seasonal_naive already uses
        # (see evaluate_all_series and deploy_forecast.py's
        # algorithm_candidates loop, both of which already skip any
        # algorithm with a NaN MAE), so no additional plumbing was
        # needed to keep it out of enrollment_count's comparison.
        "count_model": {"actual": [], "predicted": [], "lower": [], "upper": []},
    }

    for test_key in test_ordinals:
        train = series[series["period_ordinal"] < test_key]
        test_row = series[series["period_ordinal"] == test_key]
        if train.empty or test_row.empty:
            continue

        actual = float(test_row[metric].iloc[0])
        test_ds = test_row["ds"].iloc[0]

        train_prophet_df = to_prophet_frame(train, metric)
        model = fit_prophet(train_prophet_df)
        prophet_pred, prophet_lower, prophet_upper = predict_interval(model, test_ds)

        train_values = train[metric].tolist()
        naive_pred = naive_baseline(train_values)
        hist_avg_pred = historical_average_baseline(train_values)

        results["prophet"]["actual"].append(actual)
        results["prophet"]["predicted"].append(prophet_pred)
        results["prophet"]["lower"].append(prophet_lower)
        results["prophet"]["upper"].append(prophet_upper)
        results["naive"]["actual"].append(actual)
        results["naive"]["predicted"].append(naive_pred)
        # Degenerate interval by policy, not oversight -- see
        # baselines.py's module docstring ("Prediction-interval policy
        # for a deployed baseline"): a persistence/average forecast has
        # no principled distribution to derive a CI from, so
        # yhat_lower == yhat_upper == yhat here, matching exactly what
        # BaselineModel already does for deployed baseline champions.
        results["naive"]["lower"].append(naive_pred)
        results["naive"]["upper"].append(naive_pred)
        results["historical_avg"]["actual"].append(actual)
        results["historical_avg"]["predicted"].append(hist_avg_pred)
        results["historical_avg"]["lower"].append(hist_avg_pred)
        results["historical_avg"]["upper"].append(hist_avg_pred)

        # P1.16: only contribute a seasonal_naive point for folds where
        # the equivalent prior-season period actually exists in this
        # fold's training window. A series can clear MIN_HISTORY_PERIODS
        # (4 distinct periods) without those periods being contiguous
        # back to the required season lookback, so this is checked per
        # fold rather than assumed -- unlike naive/historical_avg, which
        # never need more than "train is non-empty." Skipping a fold here
        # narrows seasonal_naive's sample size, it never fabricates a
        # point; evaluate_all_series treats a series with zero eligible
        # folds as "no seasonal_naive comparison available," not an error.
        train_ordinals = train["period_ordinal"].tolist()
        try:
            seasonal_pred = seasonal_naive_baseline(
                train_ordinals, train_values, test_key, SEASON_LENGTH
            )
        except ValueError:
            pass
        else:
            results["seasonal_naive"]["actual"].append(actual)
            results["seasonal_naive"]["predicted"].append(seasonal_pred)
            results["seasonal_naive"]["lower"].append(seasonal_pred)  # degenerate, same policy as naive/hist_avg
            results["seasonal_naive"]["upper"].append(seasonal_pred)

        # P2.1: count_model, gated to graduation_count only -- see the
        # results dict comment above for why. Wrapped broadly (not just
        # ValueError) because this calls into statsmodels' numerical
        # optimizer, whose failure modes on 3-5 data points aren't
        # limited to the ValueError count_model.py itself raises for
        # malformed input; any fit failure here should skip this fold
        # the same way a missing seasonal_naive lookback does, not
        # abort the whole series' evaluation.
        if metric == "graduation_count":
            try:
                count_fit = fit_and_predict_count_model(train_ordinals, train_values, test_key)
            except Exception:
                logging.getLogger(__name__).warning(
                    "count_model fit failed for a graduation_count fold (test_key=%s, "
                    "n_train=%d) -- skipping this fold, not the series",
                    test_key, len(train_values),
                )
            else:
                results["count_model"]["actual"].append(actual)
                results["count_model"]["predicted"].append(count_fit.yhat)
                results["count_model"]["lower"].append(count_fit.yhat_lower)
                results["count_model"]["upper"].append(count_fit.yhat_upper)

    return results


def compute_metrics_for_model(
    actual: List[float], predicted: List[float],
    lower: Optional[List[float]] = None, upper: Optional[List[float]] = None,
) -> Dict[str, float]:
    # P2.1 fix: zero eligible folds (already possible for seasonal_naive
    # -- a series with no prior-season lookback value -- and now also for
    # count_model on every enrollment_count row, since it's only ever fit
    # for graduation_count; see walk_forward_evaluate) must report NaN
    # for every metric, INCLUDING r2, without calling into numpy on an
    # empty array. Previously (pre-P2.1) this fell through to
    # r_squared([], []) -- since ss_tot == ss_res == 0 for two empty
    # arrays, that returned a misleadingly "perfect" r2 of 1.0 for
    # "not evaluated," alongside a RuntimeWarning from numpy's
    # mean-of-empty-slice on every call. That r2 quirk was invisible
    # before because no caller surfaced a bare seasonal_naive_r2; P2.1
    # newly exposes count_model_r2 in evaluate_all_series' output, so
    # the quirk is now visible and worth fixing here rather than
    # inheriting it for a second algorithm.
    #
    # P0 (Full Re-run gate): lower/upper are optional so existing callers
    # (and every test written before this fix) that only pass
    # actual/predicted keep working -- coverage/width fields are simply
    # NaN/"0/0" when bounds aren't supplied, the same "not evaluated"
    # convention the rest of this function already uses.
    if len(actual) == 0:
        return {
            "mae": float("nan"), "rmse": float("nan"), "r2": float("nan"), "mape": float("nan"),
            "coverage_hits": 0, "coverage_n": 0,
            "mean_interval_width": float("nan"), "normalized_interval_width": float("nan"),
        }
    result = {"mae": mae(actual, predicted), "rmse": rmse(actual, predicted), "r2": r_squared(actual, predicted)}
    try:
        result["mape"] = mape(actual, predicted)
    except ValueError:
        result["mape"] = float("nan")  # every actual value was 0 across all folds -- disclosed, not hidden

    if lower is not None and upper is not None:
        hits, n = interval_coverage(actual, lower, upper)
        result["coverage_hits"] = hits
        result["coverage_n"] = n
        result["mean_interval_width"] = mean_interval_width(lower, upper)
        result["normalized_interval_width"] = normalized_interval_width(lower, upper, actual)
    else:
        result["coverage_hits"] = 0
        result["coverage_n"] = 0
        result["mean_interval_width"] = float("nan")
        result["normalized_interval_width"] = float("nan")
    return result


def evaluate_all_series(engine) -> pd.DataFrame:
    """Evaluate every (program, metric) combination via walk-forward
    validation, comparing Prophet against both baselines. Returns one row
    per (program, metric) with each model's metrics and a
    prophet_beats_best_baseline flag.

    Programs with fewer than MIN_HISTORY_PERIODS distinct periods are
    skipped (logged, not silently dropped) -- see module docstring."""
    df = load_series(engine)

    # A genuinely empty gold.ml_program_forecast_features (fresh database,
    # data pipeline not yet run) is a normal state, not an error --
    # .max() on an empty column is NaN, and int(NaN) raises, so this must
    # be checked before deriving fold ordinals, not left to fail there.
    if df.empty:
        logging.getLogger(__name__).info(
            "evaluate_all_series: gold.ml_program_forecast_features has no "
            "rows yet -- returning an empty report. Run the Bronze/Silver/"
            "Gold pipeline (and dbt/build_ml_features) before evaluating."
        )
        return pd.DataFrame(columns=[
            "program_id", "college_id", "metric",
            "prophet_mae", "prophet_rmse", "prophet_mape", "prophet_r2",
            "naive_mae", "historical_avg_mae", "seasonal_naive_mae",
            "best_baseline_mae", "mae_diff", "prophet_beats_best_baseline",
            # P2.1: reported alongside, NOT folded into best_baseline_mae/
            # prophet_beats_best_baseline -- count_model is a candidate
            # algorithm (like Prophet), not a baseline Prophet must beat.
            # See write_evaluation_report and select_champion_algorithm
            # (model_registry.py) for where it's actually compared.
            "count_model_mae", "count_model_rmse", "count_model_mape", "count_model_r2",
            "prophet_coverage_hits", "prophet_coverage_n",
            "prophet_mean_interval_width", "prophet_normalized_interval_width",
            "count_model_coverage_hits", "count_model_coverage_n",
            "count_model_mean_interval_width", "count_model_normalized_interval_width",
        ])

    rows = []

    # P1.14 fix: derive fold boundaries ONCE from the whole dataset's max
    # observed period_ordinal, so every program is scored against the
    # same 3 test points -- not a hardcoded [3, 4, 5] literal that would
    # silently stop matching once history grows past 6 periods.
    test_ordinals = derive_test_period_ordinals(int(df["period_ordinal"].max()))

    try:
        for program_id in sorted(df["program_id"].unique()):
            program_series = df[df["program_id"] == program_id]
            if not has_sufficient_history(program_series):
                logging.getLogger(__name__).info(
                    "Skipping program %s: only %d distinct period(s), need >= %d for one walk-forward fold",
                    program_id, program_series["period_ordinal"].nunique(), MIN_HISTORY_PERIODS,
                )
                continue

            college_id = program_series["college_id"].iloc[0]
            for metric in TARGET_METRICS:
                fold_results = walk_forward_evaluate(program_series, metric, test_ordinals)

                model_metrics = {
                    name: compute_metrics_for_model(r["actual"], r["predicted"], r["lower"], r["upper"])
                    for name, r in fold_results.items()
                }

                # P1.16/P1.17: fold seasonal_naive into "best baseline"
                # alongside naive/historical_avg -- but only when it has a
                # real value. A series with zero eligible seasonal folds
                # (see walk_forward_evaluate) produces NaN via numpy's
                # mean-of-empty-array; NaN must never enter the min(), or
                # it silently corrupts best_baseline_mae for every series
                # that lacks full seasonal coverage. Excluding it here
                # means such a series falls back to exactly the P1
                # (naive vs. historical_avg) behavior -- a strict
                # superset, never a regression.
                candidate_baseline_maes = [
                    model_metrics["naive"]["mae"],
                    model_metrics["historical_avg"]["mae"],
                ]
                seasonal_naive_mae = model_metrics["seasonal_naive"]["mae"]
                if not pd.isna(seasonal_naive_mae):
                    candidate_baseline_maes.append(seasonal_naive_mae)
                best_baseline_mae = min(candidate_baseline_maes)
                prophet_mae = model_metrics["prophet"]["mae"]
                beats_baseline = prophet_mae < best_baseline_mae
                # P1.24: baseline metric, Prophet metric, and their
                # difference all reported explicitly (not left for a
                # reader to subtract) -- negative means Prophet beat
                # the baseline by that many MAE units.
                mae_diff = prophet_mae - best_baseline_mae

                rows.append({
                    "program_id": program_id,
                    "college_id": college_id,
                    "metric": metric,
                    "prophet_mae": prophet_mae,
                    "prophet_rmse": model_metrics["prophet"]["rmse"],
                    "prophet_mape": model_metrics["prophet"]["mape"],
                    "prophet_r2": model_metrics["prophet"]["r2"],
                    "naive_mae": model_metrics["naive"]["mae"],
                    "historical_avg_mae": model_metrics["historical_avg"]["mae"],
                    "seasonal_naive_mae": seasonal_naive_mae,
                    "best_baseline_mae": best_baseline_mae,
                    "mae_diff": mae_diff,
                    "prophet_beats_best_baseline": beats_baseline,
                    # P2.1: NaN for enrollment_count rows (never fit -- see
                    # walk_forward_evaluate) and, for graduation_count rows,
                    # NaN only in the (rare, disclosed) case every fold's
                    # fit failed -- same NaN-means-"not evaluated" contract
                    # seasonal_naive_mae already uses.
                    "count_model_mae": model_metrics["count_model"]["mae"],
                    "count_model_rmse": model_metrics["count_model"]["rmse"],
                    "count_model_mape": model_metrics["count_model"]["mape"],
                    "count_model_r2": model_metrics["count_model"]["r2"],
                    # P0 (Full Re-run gate): interval coverage/width, not
                    # just point-error metrics. Reported for Prophet --
                    # the row's primary subject, matching every existing
                    # prophet_* column above -- and for count_model on
                    # graduation_count rows, since that's the other
                    # algorithm in this pipeline with a real (non-
                    # degenerate) fitted interval; naive/historical_avg/
                    # seasonal_naive's intervals are degenerate by policy
                    # (see baselines.py's module docstring) and would
                    # only ever show 0/N coverage, adding columns without
                    # adding information, so they're computed (available
                    # in the CSV via model_metrics if ever needed) but
                    # not surfaced as dedicated report columns here.
                    "prophet_coverage_hits": model_metrics["prophet"]["coverage_hits"],
                    "prophet_coverage_n": model_metrics["prophet"]["coverage_n"],
                    "prophet_mean_interval_width": model_metrics["prophet"]["mean_interval_width"],
                    "prophet_normalized_interval_width": model_metrics["prophet"]["normalized_interval_width"],
                    "count_model_coverage_hits": model_metrics["count_model"]["coverage_hits"],
                    "count_model_coverage_n": model_metrics["count_model"]["coverage_n"],
                    "count_model_mean_interval_width": model_metrics["count_model"]["mean_interval_width"],
                    "count_model_normalized_interval_width": model_metrics["count_model"]["normalized_interval_width"],
                })

        return pd.DataFrame(rows)
    except Exception as exc:
        raise ModelEvaluationError(
            f"Walk-forward evaluation failed: {exc}", stage="Model Evaluation",
        ) from exc


def train_final_models(engine, artifacts_dir: Path = DEFAULT_ARTIFACTS_DIR) -> List[str]:
    """Refit one Prophet model per (program, metric) on the FULL 6-semester
    history (not a walk-forward fold) and pickle it -- these are the
    models Day 21 loads to actually forecast period_ordinal 6 (2024-1).

    Programs with fewer than MIN_HISTORY_PERIODS distinct periods are
    skipped -- same guard as evaluate_all_series, for the same reason
    (Prophet.fit needs at least 2 rows; a fold-1-eligible series has more)."""
    df = load_series(engine)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    saved_paths = []

    for program_id in sorted(df["program_id"].unique()):
        program_series = df[df["program_id"] == program_id].sort_values("period_ordinal")
        if not has_sufficient_history(program_series):
            continue
        for metric in TARGET_METRICS:
            train_df = to_prophet_frame(program_series, metric)
            model = fit_prophet(train_df)
            path = artifacts_dir / f"{program_id}_{metric}_prophet.pkl"
            with path.open("wb") as f:
                pickle.dump(model, f)
            saved_paths.append(str(path))

    return saved_paths


def write_evaluation_report(report_df: pd.DataFrame, artifacts_dir: Path = DEFAULT_ARTIFACTS_DIR) -> Tuple[Path, Path]:
    """Write the evaluation report as both CSV (for programmatic use) and
    a human-readable Markdown summary (Day 20's deliverable)."""
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    csv_path = artifacts_dir / "evaluation_report.csv"
    md_path = artifacts_dir / "evaluation_report.md"

    report_df.to_csv(csv_path, index=False)

    total = len(report_df)
    beats_count = int(report_df["prophet_beats_best_baseline"].sum())
    # total == 0 is a legitimate state (fresh/unseeded database, or every
    # program filtered out by has_sufficient_history) -- not an error --
    # so the report must say "nothing evaluated" instead of raising
    # ZeroDivisionError on beats_count / total.
    pct_display = f"{beats_count / total:.0%}" if total else "N/A"
    lines = [
        "# Forecast Model Evaluation Report",
        "",
        f"Prophet beats the best baseline on **{beats_count} of {total}** series "
        f"({pct_display}) overall.",
        "",
    ]

    # P1.5: the combined headline above blends enrollment_count and
    # graduation_count into a single number, which hides that the two
    # metrics behave very differently -- graduation_count is low-volume
    # and spiky, so Prophet wins it far less often than enrollment_count
    # (see docs/10_Forecasting.md SS8). A reader relying on the headline
    # alone would wrongly read Prophet as uniformly ~X% reliable, when
    # the real story is metric-specific. Break it out explicitly, per
    # metric, sorted for a stable/diffable report across runs.
    if total:
        lines.append("**Breakdown by metric:**")
        lines.append("")
        for metric_name, group in report_df.groupby("metric", sort=True):
            metric_total = len(group)
            metric_beats = int(group["prophet_beats_best_baseline"].sum())
            metric_pct = f"{metric_beats / metric_total:.0%}" if metric_total else "N/A"
            lines.append(f"- `{metric_name}`: {metric_beats} of {metric_total} ({metric_pct})")
        lines.append("")
    else:
        lines.append("No series were evaluated (empty report).")
        lines.append("")

    lines += [
        "| Program | College | Metric | Prophet MAE | Prophet RMSE | 80% Interval Coverage | "
        "Mean Interval Width | Normalized Width | Naive MAE | Hist. Avg MAE | Seasonal Naive MAE | "
        "Count Model MAE | Best Baseline MAE | Diff (Prophet - Baseline) | Prophet R\u00b2 | Beats Baseline? |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    # P2.1: count_model_mae is an ADDITIVE column -- present in every
    # evaluate_all_series() output, but older/synthetic report_df
    # fixtures built before P2.1 (e.g. hand-rolled test DataFrames) may
    # not carry it. Checked once, outside the loop, rather than making
    # every row iteration re-derive "did the caller give us this
    # column" -- and rendered "n/a" either way a value isn't available:
    # column absent (not evaluated by this caller) or NaN (evaluated,
    # but graduation_count with zero successful folds, or an
    # enrollment_count row -- count_model is only ever fit for
    # graduation_count, see walk_forward_evaluate).
    has_count_model_col = "count_model_mae" in report_df.columns
    for _, row in report_df.iterrows():
        flag = "\u2705" if row["prophet_beats_best_baseline"] else "\u26a0\ufe0f NO"
        # seasonal_naive_mae is legitimately NaN for a series with no
        # eligible seasonal fold (see evaluate_all_series) -- render
        # that as "n/a", not a formatted "nan".
        seasonal_display = (
            "n/a" if pd.isna(row["seasonal_naive_mae"]) else f"{row['seasonal_naive_mae']:.2f}"
        )
        if has_count_model_col and pd.notna(row["count_model_mae"]):
            count_model_display = f"{row['count_model_mae']:.2f}"
        else:
            count_model_display = "n/a"
        # P0 (Full Re-run gate): coverage is reported as "hits/n" (a
        # fraction, not a smoothed percentage) -- with as few as 3 held-
        # out points per series, "67%" implies more precision than 2/3
        # actually carries. normalized_width is NaN (rendered "n/a") for
        # the same reason mape can be: a series whose every held-out
        # actual is 0 has no defined "relative to the actual" scale.
        coverage_display = f"{int(row['prophet_coverage_hits'])}/{int(row['prophet_coverage_n'])}"
        width_display = (
            "n/a" if pd.isna(row["prophet_mean_interval_width"]) else f"{row['prophet_mean_interval_width']:.2f}"
        )
        norm_width_display = (
            "n/a" if pd.isna(row["prophet_normalized_interval_width"])
            else f"{row['prophet_normalized_interval_width']:.2f}"
        )
        # P1.24: explicit signed difference alongside the two metrics it
        # was computed from, so acceptance/rejection is traceable from
        # the table itself, not just the boolean flag.
        lines.append(
            f"| {row['program_id']} | {row['college_id']} | {row['metric']} | {row['prophet_mae']:.2f} | "
            f"{row['prophet_rmse']:.2f} | {coverage_display} | {width_display} | {norm_width_display} | "
            f"{row['naive_mae']:.2f} | {row['historical_avg_mae']:.2f} | {seasonal_display} | "
            f"{count_model_display} | "
            f"{row['best_baseline_mae']:.2f} | {row['mae_diff']:+.2f} | "
            f"{row['prophet_r2']:.3f} | {flag} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return csv_path, md_path


# --- P1.6: graduation_count MAE-vs-MAPE reconciliation -----------------------

@dataclass(frozen=True)
class ReconciliationEntry:
    """One graduation_count series that failed to beat its baseline (no
    Prophet champion this cycle) but whose rejection looks harsher in
    MAPE than in MAE."""

    program_id: str
    college_id: str
    prophet_mae: float
    prophet_mape: Optional[float]
    best_baseline_mae: float


@dataclass(frozen=True)
class GraduationCountReconciliation:
    """summarize_graduation_count_reconciliation()'s result: how many
    graduation_count series without a Prophet champion this cycle, and
    which of those are MAE-reasonable but MAPE-ugly, at the given
    thresholds."""

    total_no_champion: int
    flagged: Tuple[ReconciliationEntry, ...]
    mae_threshold: float
    mape_threshold: float

    @property
    def count(self) -> int:
        return len(self.flagged)

    def summary_line(self) -> str:
        """One human-readable sentence for the evaluation report /
        console output -- mirrors the "N of M" phrasing write_evaluation_report
        already uses, so both are scannable the same way."""
        if self.total_no_champion == 0:
            return "No graduation_count series were without a Prophet champion this run."
        pct = f"{self.count / self.total_no_champion:.0%}"
        return (
            f"{self.count} of {self.total_no_champion} graduation_count series without a "
            f"Prophet champion ({pct}) are MAE-reasonable (\u2264 {self.mae_threshold:g} students) "
            f"but MAPE-ugly (> {self.mape_threshold:.0f}%): the absolute miss is small, the "
            "percentage looks alarming only because graduation counts are small numbers."
        )


def summarize_graduation_count_reconciliation(
    report_df: pd.DataFrame,
    mae_threshold: float = 3.0,
    mape_threshold: float = 25.0,
) -> GraduationCountReconciliation:
    """P1.6: quantifies a claim that was previously anecdotal -- that many
    graduation_count series where Prophet didn't beat the best baseline
    ("no champion") aren't actually bad forecasts, they're small-number
    MAPE distortion.

    Why this exists: metrics.mape()'s denominator is the actual value.
    A single program's graduation_count in a single semester is often a
    single- or low-double-digit number, so a Prophet MAE of, say, 2
    students against an actual of 6 reads as a 33% MAPE -- an
    attention-grabbing percentage produced by a mundane headcount miss.
    Left unexamined, a reader who scans MAPE first (or the raw
    "N of M beats baseline" headline) walks away thinking Prophet is
    failing badly on graduation forecasts, when several of those
    "failures" are actually small, defensible misses that merely look
    worse in percentage terms.

    This does NOT change any promotion decision -- MAE (not MAPE) is
    and remains the sole promotion criterion, in
    decide_promotion/decide_champion_promotion, and nothing here
    touches that logic. This function only reclassifies HOW ALARMING
    the "no champion" rejections are for a reader, and is purely
    descriptive/reporting -- an honesty aid, not a second acceptance
    gate.

    Thresholds are judgment calls, not derived constants:
      - mae_threshold=3.0 (students): "off by a handful of people, not
        dozens" for this project's typical program sizes.
      - mape_threshold=25.0 (percent): a MAPE a stakeholder would still
        call "bad" in isolation, absent this context.
    Callers evaluating institutions with very different typical program
    sizes should pass their own thresholds rather than trust these
    blindly -- see docs/10_Forecasting.md SS8 for the worked rationale.
    """
    grad = report_df[report_df["metric"] == "graduation_count"]
    # "No champion" here means Option A/Task-39 semantics: Prophet's own
    # walk-forward MAE did not beat the best available baseline for this
    # series (prophet_beats_best_baseline is False) -- i.e. no Prophet
    # model was promotable as champion for this series this cycle. This
    # is independent of Option B's per-cycle multi-algorithm winner
    # (some baseline naturally "wins" instead); the two live side by
    # side in model_registry.py, and this function only ever reads the
    # Prophet-vs-baseline comparison already recorded in report_df.
    no_champion = grad[~grad["prophet_beats_best_baseline"]]

    flagged_mask = (
        (no_champion["prophet_mae"] <= mae_threshold)
        & no_champion["prophet_mape"].notna()
        & (no_champion["prophet_mape"] > mape_threshold)
    )
    flagged_rows = no_champion[flagged_mask]

    entries = tuple(
        ReconciliationEntry(
            program_id=row["program_id"],
            college_id=row["college_id"],
            prophet_mae=float(row["prophet_mae"]),
            prophet_mape=None if pd.isna(row["prophet_mape"]) else float(row["prophet_mape"]),
            best_baseline_mae=float(row["best_baseline_mae"]),
        )
        for _, row in flagged_rows.iterrows()
    )

    return GraduationCountReconciliation(
        total_no_champion=len(no_champion),
        flagged=entries,
        mae_threshold=mae_threshold,
        mape_threshold=mape_threshold,
    )


if __name__ == "__main__":
    from pipelines.common.settings import get_postgres_settings
    from pipelines.gold.load_gold_to_postgres import build_pipeline_writer_engine

    password = get_postgres_settings().require_pipeline_writer_password()
    engine = build_pipeline_writer_engine(password)

    print("Running walk-forward evaluation...")
    report = evaluate_all_series(engine)
    csv_path, md_path = write_evaluation_report(report)
    beats = int(report["prophet_beats_best_baseline"].sum())
    print(f"Evaluation complete: Prophet beats baseline on {beats}/{len(report)} series.")
    print(f"Report written to {csv_path} and {md_path}")

    # P1.6: surface the MAE-vs-MAPE reconciliation alongside the headline,
    # so a console reader gets the same context the markdown report does.
    reconciliation = summarize_graduation_count_reconciliation(report)
    print(reconciliation.summary_line())

    print("Training final models on full history...")
    paths = train_final_models(engine)
    print(f"Saved {len(paths)} model artifacts to {DEFAULT_ARTIFACTS_DIR}")