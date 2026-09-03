# 20 — ML Assumptions

Authoritative reference for what the forecasting layer predicts, at what
grain, over what training window, how it's evaluated, and under what
conditions a new model is promoted or retrained. Grounded directly in
`models/forecasting/*.py` and `pipelines/gold/build_ml_features.py`.

## 1. Forecast Target

Two target metrics, both defined in `train_prophet.TARGET_METRICS`:

- `enrollment_count`
- `graduation_count`

These are the **only** two metrics forecasted. Both are read from
`gold.fact_institution_kpi` (college-grain) for training/evaluation
(`load_series()`), and both are also available as leakage-safe engineered
features at finer grain (see §2) for potential future model variants —
today's Prophet models train on the college-grain series only.

`enrolled` and `graduates`/`dropouts`/`shifters` from the canonical
dataset are related but distinct concepts — see
`19_Data_Contracts.md` §1.2 for the canonical schema's own metric set,
which is the mart-facing contract, not the forecasting target set.

## 2. Forecast Grain

**Model training/evaluation grain (`train_prophet.py`,
`deploy_forecast.py`):** one independent model per
**`(college, metric)`** pair — i.e. college-level, semester-frequency time
series. There is no single global model; every college × {enrollment_count,
graduation_count} combination is trained, evaluated, and promoted
independently, each with its own champion/candidate history in
`gold.model_registry`.

**Feature-engineering grain (`build_ml_features.py`)** is finer than the
model-training grain, built for future use / diagnostic purposes:

| Table | Grain | Metrics |
|---|---|---|
| `gold.ml_program_forecast_features` | `(college_key, program_key, academic_period_key)` | `enrollment_count`, `graduation_count` |
| `gold.ml_enrollment_features_by_year_level` | `(college_key, program_key, year_level_key, academic_period_key)` | `enrollment_count` only |

The year-level table is enrollment-only by design: `fact_graduation`
carries no `year_level_key`, so a shared `(..., year_level, ...)` grain
across both metrics isn't something the underlying facts actually support.
The project deliberately does not fabricate one.

**Temporal grain throughout:** semester — `period_ordinal` is the atomic
time unit everywhere in the ML layer (0-based, `(academic_year -
2021) * 2 + (semester_number - 1)`, per `academic_periods.py` and mirrored
exactly in `deploy_forecast.py::_period_ordinal`). All window functions,
walk-forward folds, and retraining comparisons order and partition by
`period_ordinal`, never by the `academic_period_key` surrogate — ordering
by a non-chronological surrogate key was an identified historical bug this
convention specifically prevents.

**Forecast horizon (P1.4, locked): next semester only.** Every deployed
forecast predicts exactly one target period ahead — `deploy_forecast.py`
builds a single-row future frame (`future = pd.DataFrame({"ds":
[target_ds]})`), never a multi-step horizon. This isn't an incidental
implementation detail; it's the project's stated objective (see
`01_Project_Overview.md` §1: *"How many students should we expect to
enroll, graduate, or drop out **next semester**?"*) and should be treated
as load-bearing wherever this project's objectives are restated —
Prophet's own multi-period forecasting capability (`make_future_dataframe`
with `periods > 1`) is deliberately unused here. Forecasting 2+ semesters
ahead is real, uncompleted future work, not an oversight — see
`14_Future_Improvements.md` §2 for the trigger condition that would
justify it.

## 3. Training Window

- **Observed history:** 4 academic years (`2021-2022` through
  `2024-2025`'s first observed periods) × 2 semesters = **8 semester
  periods**, `period_ordinal ∈ {0, ..., 7}`.
- **Final production models** (`train_final_models()`) are refit on the
  **full 8-semester history** per series — not a walk-forward fold. These
  are the artifacts a forecast run actually predicts from.
- **Walk-forward evaluation window** (used only to *measure* accuracy, not
  to produce the deployed model) uses an expanding training window with 4
  folds, each testing exactly one held-out semester strictly after its
  training data:

  | Fold | Train on `period_ordinal` | Test on `period_ordinal` (calendar) |
  |---|---|---|
  | 1 | 1–4 | 5 (2023, 1st Semester) |
  | 2 | 1–5 | 6 (2023, 2nd Semester) |
  | 3 | 1–6 | 7 (2024, 1st Semester) |
  | 4 | 1–7 | 8 (2024, 2nd Semester) |

  Each fold trains **only** on data strictly before its test point — this
  is the walk-forward discipline chosen specifically because standard
  k-fold cross-validation would shuffle future information into training
  for a time series, producing artificially optimistic accuracy.

- **Leakage-prevention constraint on engineered features
  (`build_ml_features.py`):** every feature describing period `t` uses only
  periods strictly before `t`, enforced by construction via `ROWS BETWEEN
  ... AND 1 PRECEDING` SQL window frames — not a convention that relies on
  column ordering or developer discipline. Feature families per metric:
  `_lag_1`, `_lag_2`, `_rolling_avg_2`, `_historical_avg` (expanding mean),
  `_trend` (`REGR_SLOPE` vs. `period_ordinal`), `_seasonality` (same-
  semester-number expanding mean), `_growth` (period-over-period percent
  change from lag values).
- **Reproducibility:** feature-building SQL contains no `RANDOM()` and no
  unordered aggregation; every result is fully ordered on its grain
  columns. `feature_dataset_fingerprint()` computes a stable SHA-256 hash
  (sorted by every column before hashing) so byte-for-byte reproducibility
  across runs is directly checkable, not assumed.

## 4. Model & Seasonality Assumptions

- **Algorithm:** Facebook/Meta Prophet (`fit_prophet()`), recorded in
  `gold.model_registry.algorithm` as the literal string `"prophet"`.
- **Yearly seasonality:** enabled, but with a **low Fourier order (2, vs.
  Prophet's default 10)** — chosen because the earliest walk-forward folds
  have as few as 4–7 semesters (2–3.5 years) of training data, and a
  high-order Fourier fit would overfit the small amount of seasonal signal
  actually available in that little history.
- **Weekly/daily seasonality:** disabled outright — this is semester-grain
  data; fitting sub-semester seasonality to it would be fitting noise by
  definition.
- **Date axis:** synthetic `ds` values derived purely from
  `(academic_year, semester_number)` via `semester_to_date()` (Jan 1 /
  Jul 1), not real per-student event dates — Prophet's time axis is a
  faithful stand-in for "which semester," not a claim about intra-semester
  timing.
- **Non-negativity:** forecast point estimates and both confidence-interval
  bounds are clipped to `>= 0` before being written
  (`_forecast_next_period()`) — a raw Prophet output can go negative for a
  near-zero series, and negative enrollment/graduation counts are not
  physically meaningful.

## 5. Baselines

Two required baselines, compared against Prophet on every fold
(`models/forecasting/baselines.py`):

- **Naive baseline** — predicts the most recent observed value.
- **Historical-average baseline** — predicts the mean of all training
  values seen so far.

`best_baseline_mae = min(naive_mae, historical_avg_mae)` is the bar Prophet
must clear (§7).

## 6. Evaluation Metrics

Defined in `models/forecasting/metrics.py`, computed per fold and
aggregated per `(college, metric)` series:

| Metric | Definition | Notes |
|---|---|---|
| **MAE** | Mean Absolute Error | Directly interpretable in original units (e.g. "off by ~12 students on average"). The metric promotion/retraining decisions are based on. |
| **RMSE** | Root Mean Squared Error | Penalizes large individual misses more than MAE; surfaces occasional big errors MAE would hide. |
| **MAPE** | Mean Absolute Percentage Error | Comparable across small and large series (a small college isn't penalized on the same absolute scale as CICT). Points where `y_true == 0` are excluded from the mean rather than producing `inf`/`NaN`; if **every** actual value in every fold is 0, MAPE is reported as `NaN`/`None` rather than raising — disclosed, not hidden. |
| **R²** | Proportion of variance explained vs. always predicting the mean of `y_true` | Explicitly disclosed as a **genuinely unstable statistic** with only 4 held-out points per series (this project's fold count) — reported anyway, but its instability is documented rather than treated as precise. Special-cased when `y_true` is constant: `1.0` only if the prediction matches exactly, otherwise `0.0` (avoids a `0/0` in the standard formula). |

## 7. Model Promotion Rules

Source of truth: `models/forecasting/model_registry.py::decide_promotion`
(pure function, independently unit-tested). **Both** criteria must hold:

1. **Beats the best baseline:** the candidate's MAE must be strictly less
   than `best_baseline_mae = min(naive_mae, historical_avg_mae)`.
2. **No worse than the current champion:** if a champion already exists for
   this `(college, metric)`, the candidate's MAE must be `<=` the
   champion's MAE. If no champion exists yet (first-ever run for that
   series), this criterion is trivially satisfied ("bootstrap").

If promoted: the previously-champion row (if any) is set
`is_champion = FALSE`, and the new candidate is inserted with
`is_champion = TRUE`, `promoted_at` set, `rejected_reason = NULL`. If not
promoted: the candidate is still inserted (for audit purposes — see §9),
with `is_champion = FALSE` and `rejected_reason` set to a human-readable
explanation of which criterion failed.

**Only a promoted model's forecast is written to `gold.fact_forecast`.** A
rejected candidate changes nothing in production.

Database-level enforcement backs this rule: a partial unique index
(`ux_model_registry_one_champion`, on `(college_key, metric) WHERE
is_champion`) guarantees at most one champion row per series at any time,
even against an application-level bug.

## 8. Retraining Conditions

Source of truth:
`models/forecasting/model_registry.py::should_retrain` (pure function).

- **Trigger:** a series is retrained **only** when the currently available
  maximum `period_ordinal` is strictly greater than the
  `training_data_end_period_ordinal` recorded on that series' last training
  run — i.e. a genuinely new academic year/semester has become available.
- **Explicitly NOT a trigger:** a changed row count within periods **already
  trained on** (late corrections, backfill) does not, by itself, cause a
  retrain. `should_retrain()`'s signature deliberately accepts only period
  ordinals, never a row count, so this failure mode is structurally
  impossible to trigger by accident — there is no row-count parameter to
  misuse.
- **Bootstrap case:** if no previous model exists for a series
  (`last_trained_period_ordinal is None`), retraining always proceeds.
- **Regression case:** if the current max `period_ordinal` is somehow
  *behind* the last training run's recorded end (data appears to have
  regressed), the series is **not** automatically retrained — this is
  flagged as needing investigation rather than silently retrained on
  apparently-shrunk data.
- **Gate ordering in deployment (`deploy_forecasts()`):** `should_retrain()`
  is checked **first**, before any walk-forward evaluation or model fitting
  is attempted. A series with no new period does zero additional work, not
  merely "no promotion."
- **Retraining does not guarantee promotion.** A retrained candidate still
  goes through the full evaluate → compare → promote gate in §7; retraining
  and promotion are independent decisions, both recorded per series in
  `DeploymentResult`.

## 9. Provenance & Auditability (supporting context for §7–8)

Every trained candidate — promoted or not, for every retrain attempt that
actually ran — is recorded as an insert-only row in `gold.model_registry`
with:

- `model_version` (deterministic, sortable, e.g.
  `CICT_enrollment_count_20260802T140501Z`)
- `algorithm`, `training_data_start_period_ordinal`,
  `training_data_end_period_ordinal`, `training_record_count`
- `mae`, `rmse`, `mape`, `r2`, `best_baseline_mae`, `beats_baseline`
- `is_champion`, `promoted_at`, `rejected_reason`, `artifact_path`

No row's provenance is ever overwritten — the only `UPDATE` any code path
issues is `is_champion = FALSE` on the row being demoted during a
promotion. This makes "which model generated this forecast?" answerable
directly from history, not reconstructed after the fact.

## 10. Known Limitation: Forecasted Periods Have No Dimension Row

The forecasted target period (`period_ordinal 9`, i.e. `2025, 1st
Semester`) has no corresponding row in `gold.dim_academic_period`, because
that dimension is built only from the closed, observed `ACADEMIC_YEARS =
[2021, 2022, 2023, 2024]` range (`pipelines/gold/build_dimensions.py`).
`gold.fact_forecast` therefore stores `target_academic_year` /
`target_semester_number` / `target_period_ordinal` as plain columns rather
than an `academic_period_key` foreign key. This is a disclosed, intentional
gap — not silently worked around — documented at the same level of detail
in `warehouse/ddl/008_forecast_registry.sql` and
`models/forecasting/deploy_forecast.py`.