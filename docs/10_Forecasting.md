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

## 7. Multi-Algorithm Champion Selection ("Option B")

### 7.1 What changed and why

Sections 4–5 above describe the original design: Prophet is *the* candidate, and it either beats the best baseline (naive / historical-average / seasonal-naive) or it doesn't. That framing has a gap once you look at what happens on rejection — when Prophet loses on a given `(program, metric)` series, **nothing gets deployed for it**, even though one of the baselines it lost to is, by construction, a perfectly usable forecast (it's literally "repeat last semester's value" or "repeat the same semester last year"). Rejecting Prophet was never the same thing as rejecting *forecasting* for that series, but the original pipeline behaved as if it were.

**Option B's fix:** every algorithm evaluated for a series — `prophet`, `naive`, `historical_avg`, `seasonal_naive` — competes on equal footing. Whichever has the lowest walk-forward MAE is deployed as that series' champion, whether or not it happens to be Prophet. This is implemented as an application-layer change only (see `algorithm` on `gold.model_registry`, already a free-text `VARCHAR(32)` with no `CHECK` constraint since migration `0009_model_versioning_fields.py` — no DDL migration was needed):

- `models/forecasting/baselines.py` — `BaselineModel`, a thin adapter that gives naive/historical_avg/seasonal_naive the same `.predict(future_df) -> DataFrame[yhat, yhat_lower, yhat_upper]` shape Prophet's model object already has, so `deploy_forecast.py`'s downstream code (which writes `fact_forecast` rows) doesn't need an `if algorithm == "prophet"` branch at the point of use. **Deliberate degenerate-interval policy:** `yhat_lower = yhat_upper = yhat` for baseline predictions — a baseline has no principled way to produce an 80% confidence interval the way Prophet's decomposition does, and inventing one (e.g. a flat ±N band) would misrepresent a fabricated number as a modeled uncertainty estimate. A zero-width interval is an honest signal that "no uncertainty quantification is available here," not a bug.
- `models/forecasting/model_registry.py` — `AlgorithmResult` (one algorithm's walk-forward MAE/RMSE/MAPE/R² for a series), `select_champion_algorithm()` (picks the lowest-MAE result; ties within 1e-6 broken by `ALGORITHM_SIMPLICITY_RANK`, an Occam's-razor order — `naive < seasonal_naive < historical_avg < prophet` — favoring whichever algorithm needed the least machinery to produce its prediction), and `decide_champion_promotion()`, which **supersedes** `decide_promotion()` (Section 5's original function, kept for backward compatibility, not called by `deploy_forecast.py` anymore). Critically, `decide_champion_promotion()` does *not* re-check "beats the best baseline" as a separate criterion — the winner already came out of `select_champion_algorithm()`, so it is, by construction, at least as good as every baseline evaluated that cycle (possibly because it *is* one). What's left is only the champion-vs-champion comparison: bootstrap-promote if no champion exists yet for this series, otherwise the new winner must be no worse than the currently deployed champion, regardless of which algorithm produced either one.
- `models/forecasting/deploy_forecast.py` — the per-`(program, metric)` deployment loop now builds an `AlgorithmResult` per algorithm from the walk-forward metrics already computed for that cycle, calls `select_champion_algorithm()`, and only refits on full history the algorithm that actually won (a real compute saving — Prophet is not fit at all for a series where a baseline wins). If a winning `seasonal_naive` can't find its required prior-season value at deploy time (a real edge case — see `_build_champion_model`'s docstring), the loop falls back to the next-best algorithm rather than failing the whole series.

### 7.2 What this changes for a reader of Section 5

Section 5's acceptance criteria (P1.23–P1.25) still hold — MAE is still the sole promotion signal, walk-forward validation is still required, "ran without error" is still not an acceptance criterion. What changes is the unit being promoted: it was implicitly "Prophet, or nothing" and is now "whichever algorithm actually won this cycle." A `gold.model_registry` row's `algorithm` column is no longer always `'prophet'` — a reader joining `fact_forecast` → `model_registry` should expect and handle `naive`, `historical_avg`, and `seasonal_naive` values there too, and should not assume every deployed forecast came from a fitted Prophet model.

## 8. Reporting Honesty: MAE-vs-MAPE Reconciliation for `graduation_count`

### 8.1 The problem

`write_evaluation_report()`'s headline number ("Prophet beats the best baseline on **N of M** series") blends `enrollment_count` and `graduation_count` into one figure. That's an honest total, but it obscures a real difference in how the two metrics behave: `enrollment_count` is a comparatively large, smooth number per program, while `graduation_count` is small and can look erratic from semester to semester purely because it *is* a small integer. The **per-metric breakdown** added to the evaluation report (see `models/forecasting/train_prophet.py::write_evaluation_report`, the "Breakdown by metric" section of `evaluation_report.md`) fixes the aggregation problem — `enrollment_count` and `graduation_count` are now reported separately.

That surfaces a second, more specific honesty gap: on `graduation_count`, when Prophet doesn't beat its baseline ("no champion" for that series' Prophet candidate — an Option-A/Section-5 outcome, independent of which algorithm Option B ultimately deploys), the natural next question a reader asks is *how bad*. If they reach for MAPE first, several of those "failures" look alarming — 30–40%+ percentage errors are not unusual — even though the *absolute* miss (MAE) might be a difference of only 1–3 students. This isn't a coincidence: MAPE's denominator is the actual value, and graduation counts for a single program in a single semester are frequently single- or low-double-digit numbers, so even a small absolute miss produces a large percentage.

### 8.2 The fix: `summarize_graduation_count_reconciliation()`

`models/forecasting/train_prophet.py::summarize_graduation_count_reconciliation(report_df, mae_threshold=3.0, mape_threshold=25.0)` quantifies this directly, rather than leaving it as an unverified claim in prose:

1. Filters `report_df` to `metric == "graduation_count"`.
2. Within that, filters to rows where Prophet did **not** beat its best baseline (`prophet_beats_best_baseline == False`) — the "no champion" set this function is scoped to.
3. Of those, flags the subset where `prophet_mae <= mae_threshold` **and** `prophet_mape > mape_threshold` — i.e., a small absolute miss that reads as a large percentage.
4. Returns a `GraduationCountReconciliation` (`total_no_champion`, `flagged` entries, the thresholds used, and a `.summary_line()` for direct inclusion in a report or console log).

**This does not change any promotion decision.** MAE — not MAPE — remains the sole promotion criterion in `decide_promotion()` / `decide_champion_promotion()`, and this function does not call, wrap, or influence either one. It is purely descriptive: an honesty aid for interpreting *how alarming* a rejection is, not a second acceptance gate. The default thresholds (`mae_threshold=3.0` students, `mape_threshold=25.0`%) are judgment calls tuned to this project's typical program sizes, not derived constants — a caller with different program-size expectations should pass their own.

### 8.3 Where this shows up

Wired into `train_prophet.py`'s `__main__` block: after `write_evaluation_report()` runs, the reconciliation summary line is printed alongside the beats/total headline, so a console run gets the same context a markdown reader gets from the per-metric breakdown. It is deliberately **not** folded into `write_evaluation_report()` itself — that function only reads the columns every caller of `evaluate_all_series()`'s output is guaranteed to have (it doesn't assume a `prophet_mape` column is populated), while the reconciliation function's whole purpose depends on that column. Keeping them separate, additive functions avoids coupling a generic reporting function to a graduation-count-specific interpretation.

---
*Next: `11_Data_Consumption_Contract.md` — the published interface between this service and the Web Team.*