# 13 — Best Practices

## 1. Testing Strategy (Full Detail)

| Layer | What's tested | Tool | Example |
|---|---|---|---|
| Pure transform functions | Correctness of logic in isolation | `pytest` | Given a fixed dataframe, does the cleaning function produce the expected output? |
| Bronze schema | Shape/type correctness | `pandera` | `student_id` is a non-null string, `birth_year` is int in range |
| Silver business rules | Domain correctness | Custom assertions + quarantine tests | `graduation_date >= enrollment_date` |
| Gold/warehouse | Referential integrity, uniqueness | dbt tests (`not_null`, `unique`, `relationships`) | Every `fact_enrollment.student_key` exists in `dim_student` |
| ML features | No leakage, correct math | `pytest` with hand-computed fixtures | Lag feature at semester N equals actual value at semester N-1 |
| End-to-end | Full pipeline correctness & idempotency | `pytest` integration suite against a test Docker environment | Running ingestion twice produces identical Gold row counts |

**dbt tests vs. Great Expectations — when to use which:** dbt tests are ideal for **structural** checks tightly coupled to the SQL model that produced the table (uniqueness, referential integrity, accepted values) — they live next to the model definition and run as part of `dbt test`. Great Expectations is better suited for **richer statistical/distributional checks** (e.g., "dropout rate should not exceed 40% for any college-semester, flag if it does" — a business-plausibility check, not just a structural one) that benefit from GE's expectation-suite reporting format. This project uses **dbt tests as the default**, reserving Great Expectations for the handful of plausibility checks that don't fit dbt's test grammar — using both everywhere would be redundant tooling for a solo capstone timeline.

## 2. Code Review Checklist (Apply to Your Own Work)

- **Correctness** — does the code do what the docstring/spec says, including edge cases (empty input, all-null column, single-row dataframe)?
- **Performance** — any obvious O(n²) operations where a vectorized/set-based operation would do (especially in Pandas — row-wise `.apply()` loops are a common capstone-code smell)?
- **Scalability** — would this break if row counts were 10x? 100x? (Doesn't need to handle it, but you should be able to answer the question.)
- **Security** — are SQL queries parameterized (never f-string-interpolated with raw user/config input)? Are secrets loaded from environment variables, never hardcoded?
- **Maintainability** — could someone else (or you, in 6 months) understand this without you explaining it verbally?
- **Reliability** — what happens if this step fails halfway through? Is it safe to re-run?

## 3. Git Discipline

- Conventional commit prefixes throughout the roadmap (`feat:`, `fix:`, `test:`, `docs:`, `chore:`) — makes the commit history itself a readable changelog, and is standard practice in real engineering teams.
- One logical change per commit — a commit that says `feat: build gold fact tables` should not also silently fix an unrelated Silver bug.
- Tag releases at meaningful milestones (e.g., `v0.1.0` end of Week 1, `v1.0.0` final submission).

## 4. Idempotency — Why It's Treated as Non-Negotiable

Real registrars **do** send corrected/late data. A pipeline that isn't idempotent will either duplicate rows on reprocessing or require manual cleanup every time a correction arrives — which doesn't scale and isn't trustworthy. Every write in this project uses natural-key-based `MERGE`/upsert semantics specifically so "just re-run it" is always a safe operation, at every layer.

## 5. Observability — Minimum Viable Version for a Capstone

- `pipeline_run_log` table (batch_id, stage, timing, row counts, status) — the single source of truth for "did the last run succeed, and what did it do."
- Structured JSON logs correlated by `batch_id`.
- Dagster's built-in asset materialization UI as the visual lineage/observability layer — this is "free" observability gained just by choosing an asset-based orchestrator, worth calling out explicitly as a reason for that tool choice.

## 6. Data Governance Practices Applied (Even at Capstone Scale)

- **Metric provenance**: the Success Rate formula is versioned config, not buried logic — any number can be traced to "which formula version produced this."
- **Access control**: schema-scoped roles (`03`/`06` docs) — even a solo-built system should demonstrate least-privilege thinking.
- **Data lineage**: dbt docs + Dagster asset graph make "where did this number come from" answerable by clicking through a UI, not by reading someone's memory of the code.
- **Data quality gating**: bad data is quarantined and reported, never silently dropped or silently passed through.

## 7. When (Not) to Reach for "Big Data" Tools

This project deliberately does **not** use PySpark, Kafka, or a cloud data warehouse — not from unfamiliarity, but because the data volume (tens of thousands of rows) and arrival pattern (semester-batch) don't justify their operational cost. The senior-level judgment being practiced here is: **match tool complexity to actual data shape and volume, not to what sounds impressive.** The same architecture (medallion layers, star schema, orchestrated batch pipeline) scales conceptually to a real multi-campus deployment — at that point, Spark/cloud-warehouse tools would become justified, and `14_Future_Improvements.md` names exactly that trigger condition.

## 8. Common Mistakes This Design Deliberately Avoids

| Mistake | Why it's a mistake | How this design avoids it |
|---|---|---|
| Cleaning data during ingestion | Loses the raw audit trail; conflates concerns | Bronze is untransformed; cleaning is Silver's job only |
| Computing the same metric in multiple places | Guarantees eventual disagreement between dashboard/analyst/ML | Success Rate computed exactly once, in Gold |
| Silent row drops on bad data | Hides data quality problems instead of surfacing them | Quarantine pattern with logged reasons |
| Hardcoding business rules/reference data in code | Any change requires a code deploy | Config-driven (`configs/*.yaml`) |
| Using k-fold CV on time series | Leaks future information into training, inflates apparent accuracy | Walk-forward validation only |
| Choosing tools for prestige rather than fit | Adds operational complexity without benefit | Explicit "why not X" justification for every tool choice |
| No baseline comparison for ML | Can't tell if the model is actually adding value | Naive + historical-average baselines reported alongside every forecast |

---
*Next: `14_Future_Improvements.md` — what a next iteration would add.*
