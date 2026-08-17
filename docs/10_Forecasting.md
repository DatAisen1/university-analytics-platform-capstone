# 10 — Forecasting

## 1. What We're Forecasting

Per `(college or program, target semester)`: **enrollment count**, **graduate count**, and **total student population**. These are the three metrics with a natural, continuous historical time series — **6 semesters, `2021-2022, 1st Semester` through `2023-2024, 2nd Semester`** (corrected from a previous, incorrect 8-semester model) — that administrators explicitly asked to forecast.

## 2. Feature Engineering

The ML feature table (`ml_forecast_features`, a Gold-layer table) is built with one row per `(entity, semester)` where `entity` is a college or program:

| Feature | Description | Why it matters |
|---|---|---|
| `semester_number` | 1 or 2 | Captures within-year seasonality |
| `academic_year` | `2021-2022`, `2022-2023`, or `2023-2024` | Trend anchor — a school-year label, not a bare year |
| `college` / `program` | Categorical entity key | Allows per-entity models or entity as a regressor |
| `enrollment_growth` | `(current − previous) / previous` | Direct trend signal |
| `retention_rate`, `dropout_rate`, `graduation_rate` | From `fact_institution_kpi` | Leading indicators |
| `student_progression_index` | From success-rate's Program Completion Momentum component | Structural health signal |
| `cohort_size` | Size of the entering cohort N semesters ago (aligned to nominal duration) | Predicts upcoming graduation volume specifically |
| `historical_average` | Mean of the target metric over all prior semesters | Simple baseline anchor |
| `rolling_average_2` | Mean of target metric over the trailing 2 semesters | Smooths single-semester noise |
| `lag_1`, `lag_2` | Target metric value 1 and 2 semesters prior | Classic autoregressive signal |
| `trend_component` | Linear trend coefficient fit over all prior semesters | Captures structural growth/decline separate from seasonality |
| `seasonality_component` | Average deviation of semester 1 vs semester 2 from the trend line | Isolates the semester-parity effect from trend |

**A hard limitation, now stated more strongly than before:** with only **6 semesters** of history (down from the previously-assumed, incorrect 8), there is even less data per entity than earlier drafts of this document disclosed — some programs will have fewer than 6 usable data points if they're newer or small, and `lag_2` alone consumes a third of the entire series. This bounds which models are even appropriate (see below) even more tightly than before, and bounds how much confidence any forecast should carry; this must be disclosed to the reader of the forecast, not hidden behind a confident-looking chart.

## 3. Model Comparison

| | Prophet | ARIMA/SARIMA | Linear Regression | Random Forest Regression | XGBoost | LSTM |
|---|---|---|---|---|---|---|
| Advantages | Handles trend + seasonality out of the box, interpretable components, robust to missing data | Strong classical statistical foundation, well-understood confidence intervals | Simple, highly interpretable, fast to explain to a non-technical panel | Captures non-linear interactions between features, robust to outliers | Often best raw accuracy on tabular feature sets | Can model complex sequential dependencies |
| Disadvantages | Less flexible for highly irregular/non-seasonal series | Requires careful manual tuning, sensitive to stationarity assumptions | Can't capture non-linear trend changes or seasonality without manual feature engineering | Harder to explain than linear/Prophet; needs enough data to avoid overfitting | Needs careful tuning, higher overfitting risk on very small datasets | **Needs far more data than 6 data points per series; will overfit and become an uninterpretable black box here** |
| Data requirements | Low–medium; works with just a handful of seasonal cycles | Medium–high | Low | Medium | Medium–high | **High** — even more clearly disqualifying under the corrected 6-semester model than under the old 8-semester one |
| Capstone/maintenance | **High** — one clear library, minimal per-series manual tuning | Medium — requires justifying per-series orders in a viva | High as an interpretable *baseline*, not primary | Medium, good as secondary comparison model | Medium, good as secondary comparison model | **Excluded** |

## 4. Final Model Selection: Prophet

**Decision: Prophet is the primary forecasting model.** Linear Regression is retained as an interpretable baseline for comparison during evaluation (not deployed to `fact_forecast`).

**Justification:**
1. **Data volume fit** — Prophet is explicitly designed to work well with a small number of seasonal cycles. Under the corrected model that's **6 semesters = 3 "seasonal years,"** an even thinner series than the 4 "seasonal years" this document previously (incorrectly) assumed — which makes the case *against* data-hungry models (LSTM, careful per-series ARIMA tuning) stronger, not weaker.
2. **Automatic trend + seasonality decomposition** — the semester-1-vs-semester-2 pattern is exactly the shape Prophet models natively.
3. **Interpretability for a capstone defense** — Prophet's decomposed output (trend line + seasonal component + uncertainty interval) can be shown directly on a chart and explained component-by-component to a panel.
4. **Operational simplicity** — one Prophet model can be fit per `(college, metric)` or `(program, metric)` series in a simple loop.

**Why not XGBoost/Random Forest as primary despite often-higher raw accuracy on tabular data:** with only 6 historical points per series — even fewer than the 8 this document previously assumed — there is even less basis to trust that a more flexible, higher-variance model is actually more accurate rather than just better-fit to noise. The honest answer at this (now smaller) data volume is that a simpler, structurally-motivated model is *more* trustworthy, not less capable, and the case for disclosing this limitation prominently on any forecast is stronger than it was before the calendar correction.

## 5. Model Evaluation

### Metrics Used
| Metric | What it measures | Why included |
|---|---|---|
| MAE (Mean Absolute Error) | Average absolute forecast error, in original units (students) | Directly interpretable to non-technical stakeholders |
| RMSE (Root Mean Squared Error) | Like MAE but penalizes large errors more | Surfaces whether the model has occasional big misses MAE would hide |
| MAPE (Mean Absolute Percentage Error) | Error as a % of actual value | Comparable across small vs. large programs |
| R² | Proportion of variance explained | Sanity check against a naive baseline |

### Validation Strategy: Walk-Forward (Time Series) Validation
Standard k-fold cross-validation is **not used**, because it randomly shuffles data across folds — for time series, this leaks future information into training. Instead, under the corrected 6-semester grain:

```
Train: 2021-2022 S1 → 2022-2023 S1  |  Test: 2022-2023 S2
Train: 2021-2022 S1 → 2022-2023 S2  |  Test: 2023-2024 S1
Train: 2021-2022 S1 → 2023-2024 S1  |  Test: 2023-2024 S2
```

Only **3 walk-forward folds** are available under the corrected model (down from the 4 folds a previous, incorrect 8-semester draft used) — a direct, disclosed consequence of the academic-calendar fix. Each fold trains only on data strictly before the test point, then errors are averaged across folds. With this few folds, the evaluation report should treat any per-fold metric as a point estimate with wide, disclosed uncertainty, not a precise accuracy figure.

### Baseline Comparison (Required, Not Optional)
Every Prophet forecast is compared against three baselines: a **naive baseline** (last semester's actual value), a **historical-average baseline**, and a **seasonal-naive baseline** (the same semester one academic year prior — e.g. this Fall's forecast compared against last Fall's actual value, not last Spring's). The seasonal-naive baseline is only computed for a walk-forward fold where that prior-season period actually exists in the training window; where it's unavailable (a series without a full prior seasonal cycle in its training data), the comparison falls back to naive/historical-average only for that series — this is disclosed in the evaluation report as `n/a`, not silently substituted. Prophet must beat the best of whichever baselines are available on a given series; if it doesn't, that's reported honestly rather than hidden.

**P1.24: the comparison is a structured value, not just prose.** Both `models/forecasting/train_prophet.py::evaluate_all_series` (the `mae_diff` column, `Diff (Prophet - Baseline)` in `evaluation_report.md`) and `models/forecasting/model_registry.py::decide_promotion` (`PromotionDecision.baseline_mae` / `.candidate_mae` / `.mae_diff`) report the best-baseline MAE, the Prophet/candidate MAE, and their signed difference (`candidate - baseline`; negative means Prophet won) explicitly, rather than requiring a reader to subtract two numbers out of a sentence.

### Model Acceptance Criteria (P1.23–P1.25)
A model is never accepted merely because Prophet trained and produced output — `decide_promotion` never checks "did fitting succeed," only forecasting performance:
1. **Selection rule (P1.23):** the candidate must beat the best available baseline on walk-forward MAE (lower is better) — that's the sole criterion for a series with no existing champion. Where a champion already exists, the candidate must also match or beat the champion's MAE.
2. **Baseline comparison (P1.24):** every decision — promoted or rejected — carries the baseline metric, the candidate metric, and their difference as explicit fields (see above), so any rejection is auditable without re-deriving the numbers.
3. **Minimum acceptable performance (P1.25):** "runs without error" is not an acceptance criterion anywhere in this pipeline — a candidate that trains successfully but doesn't beat its baseline is recorded (for audit trail) and explicitly rejected, never promoted.

## 5.1 Implementation Notes and Real Results

> **⚠️ STALE — pending regeneration.** The prior version of this section reported real evaluation results (e.g., "Prophet beats the best baseline on 8 of 16 series," a metric-level 100%/0% split by `enrollment_count` vs. `graduation_count`) measured against the old, incorrect 8-semester dataset with 4 walk-forward folds, at COLLEGE grain. **P1 (Data Science Recovery) additionally moved the forecast grain from `(college, metric)` to `(program, metric)`** — `train_prophet.load_series()` now reads `gold.ml_program_forecast_features` (the dedicated, leakage-safe forecast dataset built for this exact purpose, previously unused) instead of querying `gold.fact_institution_kpi` directly. Series count is now `~37 programs × 2 metrics`, not `8 colleges × 2 metrics`, and programs with fewer than 4 distinct observed periods (`MIN_HISTORY_PERIODS`) are skipped rather than crashing Prophet on too little data — both real result sets (college-grain and program-grain) must be re-produced from a live run before this section can be un-flagged; the underlying finding about graduation-count series remaining hard to beat is *expected* to still apply, but is not re-verified here.

**Modules (unaffected by the calendar fix):** `models/forecasting/metrics.py`, `models/forecasting/baselines.py`, `models/forecasting/train_prophet.py`, run via `python -m models.forecasting.train_prophet`.

**Prophet configuration, and why (unaffected by the calendar fix):** `yearly_seasonality=2` (a low Fourier order, vs. Prophet's default 10) — with as few as 2–3 training points in the earliest walk-forward fold under the 6-semester model, a high-order seasonal fit would overfit even more readily than it would have under the old 8-semester model. `weekly_seasonality`/`daily_seasonality` disabled outright, since this is semester-grain data.

**Testing:** `tests/unit/test_forecasting_metrics.py` and `tests/unit/test_forecasting_baselines.py` keep their structure; `tests/unit/test_train_prophet.py`'s regression tests need re-running (and possibly re-writing, since the fold count changed from 4 to 3, and the grain from college to program) once real results exist again.

## 6. Forecast Output

Predictions (with 80% confidence interval bounds from Prophet) are written to `gold.fact_forecast`, tagged with `model_version`, so historical forecasts remain queryable and comparable against what actually happened once real data arrives for that semester — enabling a "forecast accuracy over time" mart the Web Team can build a view on top of. `program_key` is the grain key (migration `0013_forecast_program_grain.py`); `college_key` is kept as a denormalized, nullable convenience column for college-level rollups without a join.

### Forecast Output Contract (P1)

Every row in `gold.fact_forecast` carries the required contract fields directly:

- **future academic period** — `target_academic_year`, `target_semester_number`, `target_period_ordinal`
- **prediction** — `yhat`
- **lower bound** — `yhat_lower`
- **upper bound** — `yhat_upper`
- **model version** — `model_version` (denormalized so a `fact_forecast`-only query doesn't need a join)

The recommended metadata is a join away, on `gold.model_registry` via `model_registry_key` — matching the project's own denormalization convention (only fields needed for join-free queries are duplicated onto the fact table):

- `forecast_created_at` → `fact_forecast.generated_at` (on the fact table itself)
- `training_data_start` / `training_data_end` → `model_registry.training_data_start_period_ordinal` / `training_data_end_period_ordinal`
- `model_type` → `model_registry.algorithm`
- `evaluation_metric` → `model_registry.mae` / `.rmse` / `.mape` / `.r2`
- `dataset_fingerprint` (migration `0014_dataset_fingerprint.py`) → `model_registry.dataset_fingerprint`, the `pipelines.gold.build_ml_features.feature_dataset_fingerprint()` value for the `train_prophet.load_series()` pull that trained the candidate. Computed **once per `deploy_forecasts()` run** and reused across every candidate that run trains (all of them read the same query result) — it identifies "which data snapshot trained this candidate," not "which full `gold.ml_program_forecast_features` build" (that table's own build-time fingerprint is logged separately, in the Dagster asset that constructs it, since `load_series()` only selects a subset of its columns).

---
*Next: `11_Data_Consumption_Contract.md` — the published interface between this service and the Web Team.*