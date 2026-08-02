# 01 — Project Overview

## University Academic Analytics and Forecasting System
**NEUST Sumacab Campus — Institutional Success Rate Platform**

> **Ownership boundary (updated):** This project is the **Data Engineering + Data Science service**. A separate **Web Team** owns authentication, UI, and dashboards, and consumes this service's outputs. This document, and every doc in this set, has been rewritten to reflect that boundary — see `15_Tooling_Responsibility_Matrix.md` and the "Web Team Handoff" notes added throughout.

---

## 1. What This Project Actually Is

This is **not** a dashboard project that happens to have a database behind it. It is a **data engineering platform** — ingestion, storage, transformation, modeling, and governance — with a thin analytics/ML layer on top, that ends at a **published, tested consumption contract** (`gold` + `marts` schemas). Whatever presents that data to a human — a dashboard, a report, a mobile app — is the Web Team's job, not this repo's.

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
| Multiple consumers (Web Team dashboards, ML, ad-hoc SQL) need the same numbers | A single warehouse as the "one source of truth" |

## 4. Academic Period Model (Authoritative Definition)

This is the single most-referenced fact in the whole doc set, so it's stated once, here, and every other document points back to it rather than re-defining it.

```text
ACADEMIC YEARS IN SCOPE
2021-2022
2022-2023
2023-2024

SEMESTERS PER ACADEMIC YEAR
1st Semester
2nd Semester

→ 3 academic years × 2 semesters = 6 academic semesters, total, in scope.
```

**Three distinct concepts, not interchangeable:**

| Term | Meaning | Example |
|---|---|---|
| **Academic Year** | A calendar period spanning two semesters, using NEUST's actual school-year label | `2022-2023` |
| **Semester** | 1st or 2nd, nested inside an academic year | `2022-2023, 2nd Semester` |
| **Cohort** | The group of students who *entered* in a given academic year, tracked longitudinally across every subsequent academic year they remain enrolled | "the 2021-2022 entering cohort" — some of whom are still active in 2023-2024 |

A student's *academic year* changes every year they're observed; a student's *cohort* never changes — it's fixed at entry. Conflating the two is the single most common documentation error found in the prior draft of this project (see `14_Future_Improvements.md` §0 for the migration note).

**⚠️ Migration note:** an earlier iteration of this project modeled `academic_year` as four independent single-year labels (`2021`, `2022`, `2023`, `2024`, 2 semesters each = 8 semester-periods) instead of NEUST's actual 3-year, split-label academic calendar above. That iteration's generator, Bronze/Silver/Gold pipeline, and all reported row counts were built and validated against the **old, incorrect 8-semester grain** and are now **stale**. They are being regenerated against the correct 6-semester grain in `08_Faker_Data_Generator.md` onward. Every "Implementation Notes" section in this doc set that reports concrete numbers now carries an explicit `STALE — pending regeneration` flag until it has been re-run and re-verified against the corrected model.

## 5. Year Level Coverage (Explicit, Not Freshman-Only)

Analytics must cover the full student population, not entering students alone:

```text
Freshman → Sophomore → Junior → Senior → Super Senior → Graduate
```

with branches at every year level for `Dropout` and `Shift` (program change). Full progression rules are in `08_Faker_Data_Generator.md` §4; the dimensional grain that makes every year level independently queryable is in `04_Data_Modeling.md` §3–4.

## 6. Project Objectives (Restated as Engineering Deliverables)

| Business Objective | Engineering Deliverable |
|---|---|
| Track enrollment, graduates, dropouts, shifters | Fact tables: `fact_enrollment`, `fact_graduation`, `fact_dropout`, `fact_shifter` |
| Track retention & progression across all year levels | `fact_retention`, cohort-based progression logic |
| Program/College performance | Gold aggregates + `dim_program`, `dim_college` |
| Institutional Success Rate | Weighted composite KPI, computed in Gold, stored in `fact_institution_kpi` |
| Enrollment/Graduation trend analysis | Time-series features on Gold facts |
| Forecast enrollment, graduates, population | `fact_forecast` populated by a scheduled ML job |
| KPI publication for the Web Team | Read-only `gold`/`marts` access, contract documented in `06_Data_Warehouse.md` and `11_Data_Consumption_Contract.md` |

## 7. Scope Boundaries (Explicit — Prevents Scope Creep)

**In scope (this repo, DE/DS service):**
- Batch (semester-cadence) ingestion of synthetic registrar-like data across 2021-2022, 2022-2023, 2023-2024 (6 semesters)
- Full Bronze → Silver → Gold medallion pipeline
- Star-schema warehouse
- One well-justified forecasting model
- dbt-managed marts as the **published consumption contract**
- Data quality checks, logging, basic orchestration

**Explicitly out of scope for this repo (owned elsewhere or deliberately excluded):**
- **Dashboards, UI, and authentication** — owned by the **Web Team**, who consume `gold`/`marts` read-only. This repo does not build, run, or maintain Superset/Streamlit/any dashboard tooling (see §8 below and `15_Tooling_Responsibility_Matrix.md`).
- **Streaming ingestion** — the source system is semester-cadence by nature; simulating streaming here would be fake complexity, not engineering rigor.
- **Multi-tenant / multi-university** — a real requirement would justify this; a capstone doesn't need it and it would dilute focus.
- **Full-blown MLOps (model registry, CI/CD retraining, drift monitoring)** — mentioned in Future Improvements, not built.
- **Paid cloud services** — everything must run locally/free (see `07_Technology_Stack.md`).

## 8. Service Boundary — Data Engineering/Data Science vs. Web Team

```text
DATA ENGINEERING + DATA SCIENCE (this repo)
Ingestion → Bronze → Silver → Gold → Warehouse → dbt marts → ML forecasts
    ↓
Publishes a read-only, tested, documented consumption contract
    ↓
WEB TEAM (separate repo/service)
Authentication, UI, dashboards, reports
    ↓
Consumes gold/marts only — never writes, never re-derives business logic
```

The Web Team is a **consumer**, full stop. If a number looks wrong in a Web Team dashboard, the DE/DS team's job is to make sure `gold`/`marts` is correct and explainable; presentation bugs are the Web Team's own concern.

## 9. Why "Success Rate" Needs Its Own Design (Preview)

There is no single official formula for "institutional success rate" the way there is for, say, GPA. This project **defines one explicitly** as a weighted composite of retention, graduation, dropout, shifter, and enrollment-stability sub-metrics (full derivation in `09_Data_Science.md`). Making this an explicit, documented, versioned formula — rather than an implicit downstream calculation — is itself a data governance decision: the metric can be audited, defended, and changed later without breaking historical comparability, because the *inputs* (Gold facts) are separate from the *formula* (a documented, versioned transformation).

## 10. Success Criteria for the Capstone

A finished project should let you answer, live, in front of a panel:

1. Show me last year's institutional success rate, broken down by college. *(Warehouse/marts query)*
2. Why did College X's retention drop in 2023-2024, 2nd Semester, and how do you know your number is correct? *(Data lineage: Gold → Silver → Bronze traceability)*
3. What happens if the registrar sends you the same semester's data twice? *(Idempotency demo)*
4. What's your forecast for enrollment next semester, and how confident are you? *(Forecast + evaluation metrics)*
5. If NEUST added a new campus tomorrow, what in your design would need to change? *(Extensibility argument — dimensional model, config-driven ingestion)*
6. If the Web Team's dashboard number ever disagreed with a direct SQL query against `marts`, how would you prove which one is wrong? *(Service-boundary + single-source-of-truth defense)*

## 11. Document Map

| File | Content |
|---|---|
| 02 | System Architecture (end-to-end lifecycle, DE/DS ↔ Web Team boundary) |
| 03 | Data Engineering practices (repo, config, logging, quality) |
| 04 | Data Modeling (star schema, SCDs, keys, 6-semester grain) |
| 05 | Medallion Architecture (Bronze/Silver/Gold detail) |
| 06 | Data Warehouse design + access model for the Web Team |
| 07 | Technology Stack comparison + final stack (DE/DS scope only) |
| 08 | Faker Data Generator design (6-semester, full year-level coverage) |
| 09 | Data Science — Success Rate model |
| 10 | Forecasting — model comparison & selection |
| 11 | Data Consumption Contract (formerly "Dashboard Design") |
| 12 | 30-Day Implementation Roadmap |
| 13 | Best Practices |
| 14 | Future Improvements |
| 15 | Tooling Responsibility Matrix |

---
*Next: `02_System_Architecture.md` — the full data lifecycle diagram and stage-by-stage rationale.*