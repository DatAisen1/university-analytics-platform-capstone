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