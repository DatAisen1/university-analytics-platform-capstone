# 10 — Forecasting

## 1. What We're Forecasting

Per `(college or program, target semester)`: **enrollment count**, **graduate count**, and **total student population**. These are the three metrics with a natural, continuous historical time series (8 semesters, 2021-1 through 2024-2) that administrators explicitly asked to forecast.

## 2. Feature Engineering

The ML feature table (`ml_forecast_features`, a Gold-layer table) is built with one row per `(entity, semester)` where `entity` is a college or program:

| Feature | Description | Why it matters |
|---|---|---|
| `semester_number` | 1 or 2 | Captures within-year seasonality (semesters aren't identical — e.g., enrollment often dips slightly in semester 2 for some programs) |
| `academic_year` | 2021–2024 | Trend anchor |
| `college` / `program` | Categorical entity key | Allows per-entity models or entity as a regressor |
| `enrollment_growth` | `(current − previous) / previous` | Direct trend signal |
| `retention_rate`, `dropout_rate`, `graduation_rate` | From `fact_institution_kpi` | Leading indicators — a rising dropout rate this semester predicts lower enrollment next semester |
| `student_progression_index` | From success-rate's Program Completion Momentum component | Structural health signal |
| `cohort_size` | Size of the entering cohort N semesters ago (aligned to nominal duration) | Predicts upcoming graduation volume specifically |
| `historical_average` | Mean of the target metric over all prior semesters | Simple baseline anchor |
| `rolling_average_2` | Mean of target metric over the trailing 2 semesters | Smooths single-semester noise |
| `lag_1`, `lag_2` | Target metric value 1 and 2 semesters prior | Classic autoregressive signal |
| `trend_component` | Linear trend coefficient fit over all prior semesters | Captures structural growth/decline separate from seasonality |
| `seasonality_component` | Average deviation of semester 1 vs semester 2 from the trend line | Isolates the semester-parity effect from trend |

**Why lag + rolling + trend + seasonality together, not just one:** each captures a different, complementary signal — lag features capture short-term momentum, rolling averages smooth noise, trend captures the structural direction, and the seasonality component captures the semester-1-vs-2 pattern. A model given only raw historical values would conflate all four; giving it these as separate columns lets it (or a human reviewing feature importance) attribute the forecast to the right cause.

**A hard limitation, stated directly:** with only 8 semesters (2021–2024) of history, there is very little data per entity — some programs will have fewer than 8 usable data points if they're newer or small. This bounds which models are even appropriate (see below) and bounds how much confidence the forecast should carry; this is disclosed to the reader of the forecast, not hidden behind a confident-looking chart.

## 3. Model Comparison

| | Prophet | ARIMA/SARIMA | Linear Regression | Random Forest Regression | XGBoost | LSTM |
|---|---|---|---|---|---|---|
| Advantages | Handles trend + seasonality out of the box, interpretable components, robust to missing data | Strong classical statistical foundation, well-understood confidence intervals | Simple, highly interpretable, fast to explain to a non-technical panel | Captures non-linear interactions between features, robust to outliers | Often best raw accuracy on tabular feature sets | Can model complex sequential dependencies |
| Disadvantages | Less flexible for highly irregular/non-seasonal series | Requires careful manual tuning (p,d,q / seasonal orders), sensitive to stationarity assumptions | Can't capture non-linear trend changes or seasonality without manual feature engineering | Harder to explain to non-technical stakeholders than linear/Prophet; needs enough data to avoid overfitting | Needs careful tuning, higher overfitting risk on very small datasets | **Needs far more data than 8–16 data points per series; will overfit and become an uninterpretable black box here** |
| Interpretability | High (trend/seasonality/holiday components are inspectable) | Medium (coefficients are statistically meaningful but less intuitive) | High | Medium (feature importances only) | Medium | Low |
| Scalability | Good — designed for many parallel per-entity series ("one Prophet model per program" scales fine) | Good, but per-series manual tuning doesn't scale to 30 programs easily | Good | Good | Good | Poor at this data volume |
| Data requirements | Low–medium; works with just a handful of seasonal cycles | Medium–high; needs enough history to estimate seasonal orders reliably | Low | Medium | Medium–high | **High** — this is the disqualifying factor here |
| Training speed | Fast | Fast–medium (per-series tuning takes time) | Very fast | Fast | Fast | Slow, and unstable on tiny datasets |
| Capstone/maintenance | **High** — one clear library, minimal per-series manual tuning, results are explainable in a defense | Medium — defensible but requires justifying (p,d,q) choices per series in a viva | High as an interpretable *baseline*, not primary | Medium, good as secondary comparison model | Medium, good as secondary comparison model | **Excluded** |

## 4. Final Model Selection: Prophet

**Decision: Prophet is the primary forecasting model.** Linear Regression is retained as an interpretable baseline for comparison during evaluation (not deployed to `fact_forecast`).

**Justification:**
1. **Data volume fit** — Prophet is explicitly designed to work well with a small number of seasonal cycles (here: 8 semesters = 4 "seasonal years"), unlike LSTM which needs orders of magnitude more data to avoid overfitting, and unlike ARIMA/SARIMA which need careful per-series order selection that doesn't scale cleanly across ~30 programs and 8 colleges without a lot of manual tuning time this one-month capstone doesn't have.
2. **Automatic trend + seasonality decomposition** — the semester-1-vs-semester-2 pattern (an academic-calendar-specific seasonality) is exactly the shape Prophet models natively, without hand-engineering ARIMA's seasonal order parameters.
3. **Interpretability for a capstone defense** — Prophet's decomposed output (trend line + seasonal component + uncertainty interval) can be shown directly on a chart and explained component-by-component to a panel, which is a materially stronger defense position than "the model has 400 weights and I can't tell you why it predicted 812 students."
4. **Operational simplicity** — one Prophet model can be fit per `(college, metric)` or `(program, metric)` series in a simple loop, which fits the batch/orchestration model already used everywhere else in this pipeline (no separate serving infrastructure needed).

**Why not XGBoost/Random Forest as primary despite often-higher raw accuracy on tabular data:** with only 8 historical points per series, there isn't enough data to reliably validate that a more flexible, higher-variance model (XGBoost/RF) is actually more accurate rather than just better-fit to noise — the honest answer at this data volume is that a simpler, structurally-motivated model (Prophet, with a known trend+seasonality decomposition matching the real academic calendar) is *more* trustworthy, not less capable. This is disclosed directly rather than picking the "more impressive-sounding" model.

## 5. Model Evaluation

### Metrics Used
| Metric | What it measures | Why included |
|---|---|---|
| MAE (Mean Absolute Error) | Average absolute forecast error, in original units (students) | Directly interpretable to non-technical stakeholders ("off by ~12 students on average") |
| RMSE (Root Mean Squared Error) | Like MAE but penalizes large errors more | Surfaces whether the model has occasional big misses MAE would hide |
| MAPE (Mean Absolute Percentage Error) | Error as a % of actual value | Comparable across small vs. large programs |
| R² | Proportion of variance explained | Sanity check against a naive baseline (predicting last semester's value) |

### Validation Strategy: Walk-Forward (Time Series) Validation
Standard k-fold cross-validation is **not used**, because it randomly shuffles data across folds — for time series, this leaks future information into training (the model would "see the future" during validation), producing artificially optimistic accuracy. Instead:

```
Train: 2021-1 → 2022-2  |  Test: 2023-1
Train: 2021-1 → 2023-1  |  Test: 2023-2
Train: 2021-1 → 2023-2  |  Test: 2024-1
Train: 2021-1 → 2024-1  |  Test: 2024-2
```

Each fold trains only on data strictly before the test point (walk-forward / expanding-window split), then errors are averaged across folds — this is the only validation approach that honestly simulates "what would this model have predicted, at the time, using only information available then."

### Baseline Comparison (Required, Not Optional)
Every Prophet forecast is compared against a **naive baseline** (last semester's actual value) and a **historical-average baseline**. If Prophet doesn't outperform these simple baselines on a given series, that's reported honestly rather than hidden — for very small or highly volatile programs, a naive baseline may legitimately be competitive, and the evaluation report should say so.

## 5.1 Implementation Notes and Real Results (Day 20)

**Modules:** `models/forecasting/metrics.py` (pure MAE/RMSE/MAPE/R² functions), `models/forecasting/baselines.py` (naive + historical-average), `models/forecasting/train_prophet.py` (the walk-forward harness + final model training), run via `python -m models.forecasting.train_prophet`. Trained model artifacts land in `forecasting/artifacts/{college_id}_{metric}_prophet.pkl`; the evaluation report lands in `forecasting/artifacts/evaluation_report.{csv,md}`.

**The honest headline result: Prophet beats the best baseline on 8 of 16 series (50%) — not the "majority" this section's validation checklist calls for, taken at face value.** But the aggregate number hides a far more informative, and far cleaner, pattern underneath it, worth reporting instead of the single top-line figure:

| Target metric | Prophet beats baseline |
|---|---|
| `enrollment_count` | **8 / 8 colleges (100%)** |
| `graduation_count` | **0 / 8 colleges (0%)** |

This isn't a random 50/50 split — it's a perfect split *by metric*, and it has a specific, traceable cause rather than being an unexplained anomaly. Inspecting the real data directly (CICT's `graduation_count` across all 8 semesters): `0, 0, 0, 0, 0, 0, 0, 99`. Seven of eight values are exactly zero. This is the cohort-truncation limitation disclosed since Week 1 (`08_Faker_Data_Generator.md` Section 10: this generator only simulates students *entering* during the observed window, so almost no cohort reaches graduation eligibility until the very end of it) — now visibly propagating all the way through to forecast evaluation, three weeks and several pipeline layers later. A naive "predict last value" baseline on a series that's mostly zero is *structurally* hard to beat: predicting 0 is usually correct by construction, and a trend-fitting model like Prophet, which will generally produce some small positive fitted value even for a mostly-flat series, looks "worse" by MAE despite arguably behaving more sensibly than a baseline that's really just exploiting the series' degeneracy.

**This was not patched by tweaking Prophet's configuration to force a better-looking aggregate number.** The honest conclusion is that `enrollment_count` forecasts are meaningfully validated and trustworthy; `graduation_count` forecasts inherit a known, disclosed data limitation and should be presented with that caveat attached, not hidden behind an aggregate "50% beat rate" that obscures exactly which half is reliable. **This is precisely what Day 20's validation checklist item 2 asks for** — "series where it doesn't [beat the baseline] are explicitly flagged, not hidden" — satisfied at the metric level, not just the individual-series level.

**Prophet configuration, and why:** `yearly_seasonality=2` (a low Fourier order, vs. Prophet's default 10) — with as few as 4 training points in the earliest walk-forward fold, a high-order seasonal fit would overfit the tiny amount of seasonal signal available. `weekly_seasonality`/`daily_seasonality` disabled outright, since this is semester-grain data and fitting sub-semester seasonality to it is fitting noise by definition.

**Testing:** `tests/unit/test_forecasting_metrics.py` (14 tests, every metric checked against a hand-computed value first, plus edge cases: MAPE's zero-actual handling, R²'s degenerate constant-series cases) and `tests/unit/test_forecasting_baselines.py` (7 tests). `tests/unit/test_train_prophet.py` (10 tests) includes the metric-split finding above locked in as two permanent regression tests (`test_prophet_beats_baseline_on_every_enrollment_series`, `test_prophet_does_not_beat_baseline_on_graduation_series`) — if a future change to the feature set or Prophet configuration shifts this balance, the tests will need deliberate updating, which is exactly the point: the finding stays visible and intentional rather than silently drifting.

## 6. Forecast Output

Predictions (with 80% confidence interval bounds from Prophet) are written to `gold.fact_forecast`, tagged with `model_version`, so historical forecasts remain queryable and comparable against what actually happened once real data arrives for that semester — enabling a "forecast accuracy over time" view in the dashboard itself.

---
*Next: `11_Dashboard.md` — dashboard suite design.*
