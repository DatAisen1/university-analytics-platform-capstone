# 21 — Option B Punchlist (Multi-Algorithm Champion Selection)

Status tracker for the "Option B" change described in `10_Forecasting.md`
§7–§8: letting any evaluated algorithm (not just Prophet) become a
series' deployed champion, plus the reporting-honesty follow-ups it
surfaced. No P1.1–P1.4 checklist existed anywhere in the repo prior to
this file (searched `docs/`, `notebooks/`, and code comments) — P1.5 and
P1.6 below are numbered per the in-code comments in `train_prophet.py`
and `model_registry.py` that already referenced them prospectively.

## Confirmed before implementation

- [x] No DDL migration required. `gold.model_registry.algorithm` is
  `VARCHAR(32)`, free-text, no `CHECK` constraint — migration
  `0009_model_versioning_fields.py`'s own comment anticipates a second
  algorithm being introduced. Option B is a pure application-layer
  change.

## Implemented

- [x] **`models/forecasting/baselines.py`** — `[MODIFIED]`. Added
  `BaselineModel` (Prophet-shaped `.predict()` adapter, deliberate
  degenerate `yhat_lower == yhat_upper == yhat` interval policy) and
  `build_deployable_baseline()`.
- [x] **`models/forecasting/model_registry.py`** — `[MODIFIED]`. Added
  `AlgorithmResult`, `ALGORITHM_SIMPLICITY_RANK`, `ChampionSelection`,
  `select_champion_algorithm()`, `decide_champion_promotion()`
  (supersedes `decide_promotion()`, which is kept for backward
  compatibility but is no longer called by `deploy_forecast.py`).
  `make_model_version()` now requires `algorithm` as a positional
  argument (needed for DB uniqueness once multiple algorithms can be
  evaluated per cycle for the same series).
- [x] **`models/forecasting/deploy_forecast.py`** — `[MODIFIED]`.
  Per-`(program, metric)` loop rewritten to build `AlgorithmResult`s
  from already-computed walk-forward metrics, call
  `select_champion_algorithm()`, refit-on-full-history only the
  algorithm that actually won (real compute saving), fall back
  gracefully when a winning `seasonal_naive` can't find its lookback
  value at deploy time, and record whichever algorithm won.
  `DeploymentResult` gained an `algorithm` field. `__main__` block
  reports per-metric coverage and champion-algorithm mix.
- [x] **`tests/unit/test_model_registry.py`** — `[MODIFIED]`. Updated
  `test_make_model_version_is_deterministic_and_readable` and
  `test_make_model_version_defaults_to_now_and_is_unique_across_calls`
  for the new required `algorithm` argument (both were failing with
  `TypeError` before this fix). Added
  `test_make_model_version_disambiguates_same_second_different_algorithm`
  as a same-second collision regression guard for the uniqueness
  constraint the new signature exists to satisfy.
- [x] **`tests/unit/test_deploy_forecast.py`** — checked, **no change
  needed**. `_patch_common`'s mocks already target
  `decide_champion_promotion`, not the retired `decide_promotion`; the
  module docstring's mention of the old name is contextual/historical,
  not a stale mock. 19/19 passing as-is.
- [x] **P1.5 — per-metric breakdown in `write_evaluation_report()`**
  (`models/forecasting/train_prophet.py`, `[MODIFIED]`). The combined
  "N of M" headline is kept (relabeled "overall") and a new "Breakdown
  by metric" section is inserted before the detail table, computed via
  `report_df.groupby("metric")` so each metric's beat-rate is computed
  against its own subtotal, not the grand total. `total == 0` (empty
  report) is handled without `ZeroDivisionError`.
  `tests/unit/test_train_prophet.py` — `[MODIFIED]`: added
  `TestWriteEvaluationReportMetricBreakdown` (3 tests, synthetic
  DataFrames, no Postgres needed) including a regression guard against
  computing a metric's percentage from the wrong denominator.
- [x] **P1.6 — `summarize_graduation_count_reconciliation()`**
  (`models/forecasting/train_prophet.py`, same file, `[MODIFIED]`
  further). New `ReconciliationEntry` / `GraduationCountReconciliation`
  dataclasses and `summarize_graduation_count_reconciliation()`
  function: filters `graduation_count` rows with no Prophet champion
  this cycle, flags the subset that's MAE-reasonable (≤3 students,
  default) but MAPE-ugly (>25%, default), and exposes a `.summary_line()`.
  Explicitly reporting-only — does not touch `decide_promotion` /
  `decide_champion_promotion`. Wired into `__main__` so a console run
  prints it alongside the headline. Kept as a separate function from
  `write_evaluation_report()` rather than merged into it, since the
  reconciliation logic depends on a `prophet_mape` column that not
  every caller of `write_evaluation_report()` is guaranteed to populate.
  `tests/unit/test_train_prophet.py` — `[MODIFIED]` further: added
  `TestGraduationCountReconciliation` (4 tests) covering the flagging
  logic, NaN-MAPE safety, the zero-no-champion edge case, and custom
  thresholds.
- [x] **`docs/10_Forecasting.md`** — `[MODIFIED]`. Added §7
  ("Multi-Algorithm Champion Selection ('Option B')") and §8
  ("Reporting Honesty: MAE-vs-MAPE Reconciliation for
  `graduation_count`"), describing what changed, why, and what it means
  for a reader of the original §4–§5 design. Previously the file ended
  at §6.
- [x] **`docs/21_Option_B_Punchlist.md`** — `[CREATED]` (this file).

## Verification performed

- Full `tests/unit/` suite (`python -m pytest tests/unit/`, all core
  deps installed): **474 passed, 35 skipped, 0 failed**, excluding
  `tests/unit/test_orchestration.py` (5 tests) which requires the
  `dagster` package — not installed in this environment and unrelated
  to this change; not touched by any edit in this punchlist.
- `py_compile` clean on every modified `.py` file.
- Live-Postgres-gated tests (`TestPostgresIntegration` in
  `test_train_prophet.py`, `test_database_constraints.py`, etc.) were
  **not** run — no reachable Postgres instance in this environment.
  They should be re-run against a real database before this work is
  considered fully verified end-to-end; the design and unit-level
  behavior are confirmed, the live DB round-trip (uniqueness
  constraint, actual `INSERT`/`SELECT` against `gold.model_registry`)
  is not.

## Discovered, out of scope for this pass

- `docs/20 ml assumptions.md` §1 still describes `load_series()` as
  reading `gold.fact_institution_kpi` at college grain. The actual
  implementation (per `train_prophet.py`'s own module docstring, a P1
  fix predating this Option B work) reads
  `gold.ml_program_forecast_features` at program grain. This looks like
  a doc that wasn't updated when the grain moved, independent of
  anything in this punchlist — worth a follow-up correction pass, not
  fixed here to keep this change scoped to Option B and its reporting
  follow-ups.

## P2.1 follow-up: `count_model` (Poisson / Negative Binomial), added to the Option B pool

Measured, not speculative: `forecasting/artifacts/evaluation_report.md`
showed Prophet beating baseline on 0 of 37 `graduation_count` series
(vs. 29 of 37 for `enrollment_count`, untouched by this work) — and,
on the nonzero subset specifically, losing badly (negative R² in the
double digits), the signature of a Gaussian trend model fit to small
right-skewed counts. See `docs/10_Forecasting.md` §9 for the full
writeup.

- [x] **`models/forecasting/count_model.py`** — `[CREATED]`. Poisson GLM
  with automatic Negative-Binomial fallback under detected
  overdispersion, degenerate-zero handling, intercept-only fallback for
  thin folds, a Prophet-shaped `.predict()` adapter
  (`CountModel`/`build_deployable_count_model`) matching
  `baselines.BaselineModel`'s pattern.
- [x] **`models/forecasting/train_prophet.py`** — `[MODIFIED]`. Wired
  `count_model` into `walk_forward_evaluate` as a new candidate bucket,
  gated so it's only ever fit when `metric == "graduation_count"`; added
  `count_model_mae/rmse/mape/r2` columns to `evaluate_all_series`'s
  output; `write_evaluation_report()`'s markdown table gained a "Count
  Model MAE" column (additive — renders `n/a` for callers/rows without
  it, so pre-P2.1 fixtures don't break); fixed a latent bug in
  `compute_metrics_for_model` where a zero-fold case (pre-existing for
  `seasonal_naive`, now also relevant for `count_model` on
  `enrollment_count` rows) silently produced a misleading `r2=1.0` with
  numpy warnings instead of honest `NaN`.
- [x] **`models/forecasting/model_registry.py`** — `[MODIFIED]`. Added
  `count_model` to `ALGORITHM_SIMPLICITY_RANK` at rank 3 (between
  `historical_avg` and `prophet`) — more machinery than a stored mean,
  less than Prophet's full decomposition — so it participates correctly
  in `select_champion_algorithm()`'s tie-break rule, not just its MAE
  comparison.
- [x] **`models/forecasting/deploy_forecast.py`** — `[MODIFIED]`.
  `_build_champion_model` gained a `count_model` dispatch branch calling
  `build_deployable_count_model`. Without this, a `count_model` win
  would have fallen through to `build_deployable_baseline` with an
  unrecognized algorithm name and raised at deploy time — found and
  fixed before it could happen, not discovered by a failed deploy.
- [x] **`requirements.txt`** — `[MODIFIED]`. Added `statsmodels==0.15.0`
  (the GLM/NB fitting library) and `scipy==1.17.1` (Poisson/NB quantile
  functions) as explicit, pinned, commented dependencies — `scipy` was
  previously only an unpinned transitive dependency of `prophet`/
  `scikit-learn`, despite `count_model.py` importing it directly.
- [x] **`tests/unit/test_count_model.py`** — `[CREATED]`. 12 tests:
  input validation, the degenerate-all-zero case, intercept-only
  fallback (both "too few points" and "no ordinal variation" trigger
  paths), trend extrapolation, a real (not mocked) overdispersion → NB
  fallback, a mocked forced-NB-failure → graceful Poisson fallback, the
  deployment adapter's tiling behavior, and a cross-module regression
  guard asserting `count_model.ALGORITHM_NAME` and
  `model_registry.ALGORITHM_SIMPLICITY_RANK`'s key for it agree — a
  silent mismatch there wouldn't error, it would just never let
  `count_model` win a tie, which is exactly the kind of failure worth a
  named test rather than trusting convention.
- [x] **`tests/unit/test_train_prophet.py`** — `[MODIFIED]`. Added
  `TestCountModelWiring` (4 tests) covering the CONTRACT between
  `train_prophet.py` and `count_model.py` specifically (not
  `count_model.py`'s own fitting logic, already covered above): the
  metric gate is real at runtime, the empty-fold case for
  `enrollment_count` doesn't crash or misreport, and predictions are
  non-negative by construction at the `walk_forward_evaluate` call site,
  not just in `count_model.py`'s own isolated tests.
- [x] **`docs/10_Forecasting.md`** — `[MODIFIED]`. Added §9
  ("A Fourth Algorithm: `count_model` for `graduation_count` ('P2.1')"),
  describing the measurement that justified it, the small-sample design
  choices, and how it competes inside the existing Option B framework
  (no new promotion pathway).
- [x] **`docs/21_Option_B_Punchlist.md`** — `[MODIFIED]` (this section).

### Verification performed

- `tests/unit/test_count_model.py`: **12 passed**, standalone.
- `tests/unit/test_train_prophet.py`: **35 passed, 5 skipped**
  (Postgres-gated, expected in an environment without a reachable
  instance), full file — confirms the new `TestCountModelWiring` class
  didn't regress anything already in this file.
- `py_compile` / `ast.parse` clean on every new/modified `.py` file.
- **Not yet run at the time of writing this section:** the full
  `pytest tests/` suite (unit + integration + dbt-dependent) against a
  live Postgres, and a real `dagster job execute -f
  orchestration/definitions.py -j full_pipeline_job` run to confirm
  `count_model` can actually win champion selection and deploy end-to-end
  against real `graduation_count` data, not just pass isolated unit
  tests. This is the same category of gap the original Option B
  punchlist above honestly flagged and left open — closing it is the
  next step, not assumed complete by the unit-level results above.