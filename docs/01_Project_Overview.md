# 01 — Project Overview

## University Academic Analytics and Forecasting System
**NEUST Sumacab Campus — Institutional Success Rate Platform**

---

## 1. What This Project Actually Is

This is **not** a dashboard project that happens to have a database behind it. It is a **data engineering platform** — ingestion, storage, transformation, modeling, and governance — with a **thin analytics/ML layer on top**. The dashboard is the last 10% of the work, not the point of it.

Framing it this way matters for a capstone because graders and interviewers alike will ask: *"What did you actually engineer?"* The answer needs to be: a governed, layered, testable, reproducible data pipeline that turns raw semester enrollment extracts into trustworthy institutional KPIs and forecasts — not "I made some charts."

## 2. Business Problem

University administrators currently look at academic performance semester-by-semester, college-by-college, with no unified, historically consistent way to answer:

- Is our overall **institutional success rate** improving or declining?
- Which colleges/programs are **retention risks**?
- How many students should we **expect to enroll, graduate, or drop out next semester**?
- Are enrollment trends **structural** (declining program demand) or **noise** (one bad semester)?

These are longitudinal, multi-dimensional questions. Spreadsheets answer single-semester questions well and multi-year, multi-dimensional questions poorly — this is exactly the problem class a dimensional warehouse exists to solve.

## 3. Why This Is a Real Data Engineering Problem (Not Just a BI Problem)

| Symptom in raw university data | Data engineering response |
|---|---|
| Registrar data arrives per semester, in inconsistent shapes | Batch ingestion contract + schema validation (Bronze) |
| Student status changes over time (enrolled → shifted → graduated) | Slowly Changing Dimensions (Silver/Gold) |
| Duplicate/late/corrected records are common in real registrars | Idempotent, replayable pipelines |
| "Success rate" isn't a single column anywhere | Derived Gold-layer metric, computed once, trusted everywhere |
| Forecasts need clean historical time series | Feature engineering on top of a conformed Gold layer |
| Multiple consumers (dashboard, ML, ad-hoc SQL) need the same numbers | A single warehouse as the "one source of truth" |

## 4. Project Objectives (Restated as Engineering Deliverables)

| Business Objective | Engineering Deliverable |
|---|---|
| Track enrollment, graduates, dropouts, shifters | Fact tables: `fact_enrollment`, `fact_graduation`, `fact_dropout`, `fact_shifter` |
| Track retention & progression | `fact_retention`, cohort-based progression logic |
| Program/College performance | Gold aggregates + `dim_program`, `dim_college` |
| Institutional Success Rate | Weighted composite KPI, computed in Gold, stored in `fact_institution_kpi` |
| Enrollment/Graduation trend analysis | Time-series features on Gold facts |
| Forecast enrollment, graduates, population | `fact_forecast` populated by a scheduled ML job |
| KPI Monitoring | Executive/KPI dashboard reading only from Gold/Warehouse — never Bronze/Silver |

## 5. Scope Boundaries (Explicit — Prevents Scope Creep)

**In scope:**
- Batch (semester-cadence) ingestion of synthetic registrar-like data (2021–2024)
- Full Bronze → Silver → Gold medallion pipeline
- Star-schema warehouse
- One well-justified forecasting model
- One deployed dashboard suite
- Data quality checks, logging, basic orchestration

**Explicitly out of scope (and why):**
- **Streaming ingestion** — the source system is semester-cadence by nature; simulating streaming here would be fake complexity, not engineering rigor.
- **Multi-tenant / multi-university** — a real requirement would justify this; a capstone doesn't need it and it would dilute focus.
- **Full-blown MLOps (model registry, CI/CD retraining, drift monitoring)** — mentioned in Future Improvements, not built, because the capstone's grading center of gravity is the data pipeline, not ML infra.
- **Paid cloud services** — everything must run locally/free (see `07 - Technology Stack`).

## 6. Why "Success Rate" Needs Its Own Design (Preview)

There is no single official formula for "institutional success rate" the way there is for, say, GPA. This project **defines one explicitly** as a weighted composite of retention, graduation, dropout, shifter, and enrollment-stability sub-metrics (full derivation in `09_Data_Science.md`). Making this an explicit, documented, versioned formula — rather than an implicit dashboard calculation — is itself a data governance decision: the metric can be audited, defended, and changed later without breaking historical comparability, because the *inputs* (Gold facts) are separate from the *formula* (a documented, versioned transformation).

## 7. Success Criteria for the Capstone

A finished project should let you answer, live, in front of a panel:

1. Show me last year's institutional success rate, broken down by college. *(Warehouse query / dashboard)*
2. Why did College X's retention drop in 2023-2, and how do you know your number is correct? *(Data lineage: Gold → Silver → Bronze traceability)*
3. What happens if the registrar sends you the same semester's data twice? *(Idempotency demo)*
4. What's your forecast for enrollment next semester, and how confident are you? *(Forecast + evaluation metrics)*
5. If NEUST added a new campus tomorrow, what in your design would need to change? *(Extensibility argument — dimensional model, config-driven ingestion)*

## 8. Document Map

| File | Content |
|---|---|
| 02 | System Architecture (end-to-end lifecycle) |
| 03 | Data Engineering practices (repo, config, logging, quality) |
| 04 | Data Modeling (star schema, SCDs, keys) |
| 05 | Medallion Architecture (Bronze/Silver/Gold detail) |
| 06 | Data Warehouse design |
| 07 | Technology Stack comparison + final stack |
| 08 | Faker Data Generator design |
| 09 | Data Science — Success Rate model |
| 10 | Forecasting — model comparison & selection |
| 11 | Dashboard design |
| 12 | 30-Day Implementation Roadmap |
| 13 | Best Practices |
| 14 | Future Improvements |

---
*Next: `02_System_Architecture.md` — the full data lifecycle diagram and stage-by-stage rationale.*
