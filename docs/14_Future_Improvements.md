# 14 — Future Improvements

This is a deliberate parking lot: ideas that are **correctly excluded** from the one-month capstone scope, along with the specific trigger condition that would justify building them. Naming these explicitly demonstrates the design was scoped on purpose, not limited by unawareness.

## 1. Scale-Triggered Improvements

| Improvement | Trigger condition | What it would replace |
|---|---|---|
| Migrate transform engine to PySpark | Data volume grows to millions of rows (e.g., multi-campus, multi-university consortium) | DuckDB/Pandas transforms |
| Move warehouse to a distributed cloud DW (BigQuery/Redshift/Snowflake) | Concurrent analytical query load or data volume exceeds single-node Postgres comfort zone | PostgreSQL |
| Introduce a proper feature store (e.g., Feast) | Multiple ML models need to share and version features consistently across teams | Ad hoc `ml_forecast_features` table |
| Streaming ingestion (Kafka/Debezium CDC) | Source SIS moves to producing real-time transactional events instead of semester batch exports | Batch ingestion |
| **Legacy/backfill entry cohorts (2018–2020)** | **Found during Day 5 implementation, not originally scoped** — see below | Entry-cohort-only population in `data_generator` |

### 1.1 Legacy Cohort Backfill (found Day 5)

The current student/progression generator only simulates students **entering** during the observed 2021–2024 window, so 2021-1 contains zero continuing upperclassmen — unrealistic for a real, ongoing university, and it measurably suppresses graduation counts and skews them toward short programs (see `08_Faker_Data_Generator.md` Section 10 for the actual numbers: 965 graduation events observed vs. ~1,500–2,500 estimated, 5-year programs producing zero graduates at all within the window).

**Proposed fix:** generate additional entry cohorts for 2018–2020, run the same `simulate_student` engine across their full history, but suppress (don't write) any record dated before 2021-1 — so those students appear in the observed data only as already-in-progress continuing students starting exactly at the window's edge, exactly as a real registrar extract would show them. This is a well-understood pattern for left-censored/truncated observation windows and would meaningfully improve realism for retention, year-level distribution, and graduation-rate KPIs — all explicit project objectives — without changing anything about the Bronze/Silver/Gold pipeline mechanics that are the actual grading focus. Deferred specifically to avoid expanding Day 5's scope mid-roadmap; a good candidate for a post-submission iteration or an explicitly-scheduled make-up day if time permits before Day 30.

## 2. MLOps Maturity

- **Multi-period-ahead forecasting** (2+ semesters out, not just next
  semester). **Deliberately out of scope, locked as of P1.4** — see
  `20_ML_Assumptions.md` §2. Trigger condition: a real administrative
  use case that specifically needs a 2+ semester runway (e.g. multi-year
  capacity planning) rather than the next-semester staffing/resourcing
  question this project was actually asked to answer, combined with
  enough walk-forward history to evaluate multi-step error honestly
  (a 1-step model's demonstrated accuracy says nothing about 2-step
  accuracy without separately validating it). What it would replace:
  `deploy_forecast.py`'s single-row `future` frame with Prophet's own
  `make_future_dataframe(periods=N)`, plus a walk-forward evaluation
  redesign to score multi-step error specifically, not just repurpose
  the existing 1-step folds.
- **Model registry** (e.g., MLflow) to version and compare forecasting models over time, rather than a single `model_version` tag in `fact_forecast`.
- **Automated retraining pipeline** triggered each semester as new actuals arrive, with drift detection comparing forecast error trends over time.
- **A/B comparison harness** to formally compare Prophet against XGBoost/LSTM once enough historical semesters accumulate (5+ years) to make more data-hungry models viable, rather than disqualifying them permanently.

## 3. Data Governance Maturity

- **Data catalog** (e.g., OpenMetadata/DataHub) for organization-wide discoverability once more teams/consumers exist beyond this single capstone.
- **PII handling formalization** — if real (not synthetic) student data were ever used, this would require a full data privacy/PII classification pass (field-level tagging, access auditing, anonymization for non-authorized roles) that synthetic data doesn't currently require.
- **SLA-based data quality alerting** (e.g., PagerDuty/Slack integration on quarantine-rate thresholds) instead of log-based monitoring.

## 4. Analytics Maturity

- **Self-service semantic layer** (e.g., dbt Semantic Layer / Cube) so analysts can query consistent metric definitions without needing to know the underlying mart SQL.
- **Natural-language query interface** over the warehouse for non-technical administrators.
- **Automated anomaly detection** on KPI trends (e.g., alert if a college's success rate drops more than 2 standard deviations semester-over-semester) rather than requiring a human to notice it on a dashboard.

## 5. Multi-Campus Extensibility

The current model already anticipates this: `dim_college` and `dim_program` are config-driven and campus-agnostic in structure. Adding a second campus would require:
1. Adding a `dim_campus` dimension and a `campus_key` FK on relevant facts.
2. Re-running the Success Rate formula per-campus (already parameterized by config, not hardcoded).
3. No change needed to Bronze/Silver cleaning logic, since it operates per-source-file regardless of which campus produced it.

This is called out specifically to demonstrate that the star-schema and config-driven design decisions in `04_Data_Modeling.md` and `03_Data_Engineering.md` were made with this extension path in mind, not accidentally compatible with it.

## 6. Honest Limitations of the Current Design (Stated Directly)

- The Success Rate formula's weights are a documented judgment call, not an empirically validated model — a real deployment would ideally calibrate weights against actual downstream outcomes (e.g., alumni employment data) over multiple years, which doesn't exist yet.
- **Forecast horizon is deliberately locked to next-semester-only** (P1.4) — see `20_ML_Assumptions.md` §2 and §2 above for what a longer horizon would need. This is stated as a design decision, not an accuracy claim about what Prophet *could* do if pointed at a longer horizon.
- Forecasting confidence is inherently limited by only 8 semesters of history — this is disclosed on the Forecast Dashboard itself, not just in this document.
- Synthetic data, however realistically modeled, cannot capture every real-world data pathology a live registrar integration would surface (e.g., true system-level data entry errors, legacy system quirks). The pipeline is built to be **robust to the kinds of messiness it was designed to simulate** — a real deployment would need a discovery phase against the actual source system before assuming the same validation rules suffice.
- **Documentation lags the dataset extension (found during P1.4, not fixed here — logged, not silently patched).** This project's dataset was extended from 3 academic years / 6 semesters (`2021-2022` through `2023-2024`) to 5 academic years / 10 semesters (`2021-2022` through `2024-2025`) as part of a P0 gate, and the extension was verified end-to-end against a live pipeline run. Several docs in this set (`01_Project_Overview.md`, `04_Data_Modeling.md`, `09_Data_Science.md`, `10_Forecasting.md`, `13_Best_Practices.md`) still describe the old 6-semester/3-year model, including specific stale numbers (e.g. "48 rows" for `fact_institution_kpi`, which should now reflect 10 semesters). `20_ML_Assumptions.md` has a second, independent staleness bug beyond period count: it describes training as reading `gold.fact_institution_kpi` (college-grain), but `train_prophet.py::load_series()` actually reads `gold.ml_program_forecast_features` (program-grain) — a P1-era fix this doc never caught up to. Deliberately scoped out of P1.4 itself (which only needed to lock horizon language, not resync every historical claim) rather than silently expanded — a full doc-sync pass across all six files is real, tracked work for a future session, not something to assume is "basically fine" because most of the numbers are close.

## 7. Week 1 Retrospective (Days 1–7)

**What shipped:** repo scaffold + Docker Compose (Postgres/MinIO) + validated reference-data config + a full three-stage synthetic data generator (students → progression → noise), 87 passing tests, all committed in small, correctly-scoped commits.

**Real problems caught and fixed while building, not designed away in advance:**

1. **The `faker/` naming collision (Day 4).** A folder literally named `faker/` at the repo root silently shadows the installed `faker` PyPI package the moment it becomes a real Python package, because repo root sits ahead of site-packages on `sys.path` (via `pytest.ini`'s `pythonpath = .`, added Day 3). Caught by deliberately testing the import *before* writing generator code against the name, not after something broke. Fixed by renaming to `data_generator/`. **Lesson:** when a project's own module name might collide with a dependency's name, test the import in isolation before building on top of it — don't assume it's fine because it hasn't broken yet.

2. **Silent `sed` failures (Day 4).** Two doc-path fixes via `sed` reported success (exit code 0) but did nothing, because the pattern contained a Unicode box-drawing character (`├──`) that broke sed's basic regex silently. Only caught by diffing staged changes before committing rather than trusting the command's exit code. **Lesson:** a command returning success is not proof it did what you meant, especially for text substitution on non-ASCII content — check the actual result.

3. **The cohort-truncation gap (Day 5).** The progression engine only simulates students *entering* during 2021–2024, so 2021-1 has zero continuing upperclassmen — unrealistic, and it suppresses graduation counts (965 actual vs. ~1,500–2,500 estimated) and skews them toward short programs. Found by actually running the generator and inspecting real output distributions, not by reviewing the design on paper. **Lesson:** a design that looks complete on paper can still have a structural gap that only shows up once you look at what it actually produces — validate against real generated output, not just against the plan. Fix proposed and deferred (Section 1.1 above) rather than silently patched by tweaking probabilities to hit a target number, which would have hidden the real cause behind numbers that merely looked right.

4. **The eligible-denominator mistake (Day 6).** A first pass compared the late-correction rate (3% target) against *all* rows and got 2.45%, which looked like a bug. It wasn't — rows in the last partition (2024-2) have no later partition to be corrected into, so they're structurally ineligible and were inflating the denominator. Recomputing against eligible rows only gave 2.99%. **Lesson:** before comparing an observed rate to a target, check whether every item in your denominator was actually eligible to be selected — an off-by-population-definition bug looks exactly like a real bug until you check.

**What's genuinely ready for Week 2:** the full generation pipeline (`generate_students` → `generate_progression` → `apply_noise`) runs cleanly and deterministically from a fresh clone with zero uncommitted state, confirmed by actually cloning the repo to a separate directory and running it there — not assumed from "it worked when I ran it." 33,800 enrollment rows, 9 realistic `enrollment_status` text variants, zero FK violations. This is real, messy-but-valid data for Bronze ingestion to start on.

**What's honestly not ready:** the cohort-truncation gap (#3 above) means graduation-rate and year-level-distribution KPIs will look thin and skewed once Gold aggregates them, particularly for 4-5 year programs. This doesn't block Week 2's Bronze/Silver/Gold mechanics — the pipeline will process what exists correctly either way — but it's a known, disclosed limitation on the *data's* realism, not the *pipeline's* correctness, and worth revisiting before treating any KPI trend as a real finding rather than a mechanics demo.

## 8. Week 2 Retrospective (Days 8–14)

**What shipped:** the full medallion pipeline, real and running end-to-end against the actual dataset — Bronze ingestion (idempotent, schema-validated), Silver cleaning + dedup + quarantine (32,701 rows reconstructed exactly from 33,800 noisy rows), and Gold (dimensions with real SCD2, all five fact tables, and the Success Rate KPI) — 221 passing tests, 7 more clean commits.

**The MinIO/Postgres constraint, handled the same honest way all week:** this sandbox has no Docker daemon, so nothing this week ran against real MinIO or Postgres. Every stage was built against a real interface (`ObjectStorage`) with a local-filesystem implementation used for actual development, plus (for storage specifically) a `boto3`-backed implementation proven correct against a *mocked* S3 backend (`moto`) rather than skipped or faked. DuckDB — genuinely in-process, no server needed — did double duty as both the transformation engine (as the tech stack always specified) and the `pipeline_run_log` metadata store. Nothing here needs to be rewritten once Postgres comes online in Week 3; only the write target changes.

**Three more real bugs, found by running against real data, not by reviewing the design:**

1. **The SCD2 entry-semester-shift bug (Day 12).** A student who shifts programs in their *very first* observed semester has no valid "prior period" to close a dimension row at — the first implementation silently produced a "closed" row with a nonsensical null `_valid_to_semester_key` for 35 of 352 shifters. Caught by checking the actual count of null-valid-to rows (35, not the expected 0), not by re-reading the SCD2 logic and convincing myself it looked right. Fixed, and permanently guarded by a regression test.
2. **The missing `college_key` on `fact_shifter` (Day 14).** A shift event spans two programs — possibly two different colleges — so there's no single unambiguous college for the fact row itself. The KPI aggregation crashed outright (`KeyError: 'college_key'`) the first time it tried to group shifter counts by college. Fixed by attributing a shift to the *from*-college (the population actually being depleted), resolved via a join against `dim_program`, and locked in as a regression test.
3. **(Carried from Week 1, still open)** The cohort-truncation gap continues to surface exactly where predicted: CICT's graduation_rate at 2023-1 is `0.0`, not because the pipeline is wrong, but because no 4-year program has reached eligibility yet at that point in the observed window. Seeing this land correctly in a real KPI table — rather than as an abstract caveat in a doc — is a useful confirmation that the mechanics are honest about the data's limitation rather than papering over it.

**The single most convincing proof point across both weeks:** 33,800 noisy Bronze-derived enrollment rows → Silver dedup → **32,701 rows, an exact match to Day 5's pre-noise ground truth**, with the composite Success Rate formula then independently reproducible from its own stored component values on a real, spot-checked college-semester row. Every layer's output can be explained and reconstructed from the layer before it — which is the entire practical meaning of "the data is trustworthy" that `13_Best_Practices.md` argues for, demonstrated rather than just asserted.

**What's ready for Week 3:** dbt models can now be built directly against genuine Gold Parquet output (once materialized into Postgres) instead of against hypothetical schemas — the shapes, keys, and row-count relationships are all proven, not just documented.

---
*This concludes the documentation set. See `README.md` for the top-level index and quick-start.*