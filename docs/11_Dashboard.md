# 11 — Data Consumption Contract (formerly "Dashboard Design")

> **This document replaces the previous `11_Dashboard.md`.** This repo (Data Engineering + Data Science) does not build, own, or operate dashboards. That responsibility belongs to the **Web Team**. This document defines the *contract* between the two teams — what's published, how it's accessed, and what questions it's designed to answer — so the Web Team can build whatever presentation layer they choose against a stable, tested, documented interface.

## 1. Design Principle

**The Web Team reads `gold`/`marts` only, through the `web_service_reader` role, and contains zero business logic of its own.** Every number it displays was already computed, tested, and validated upstream (`fact_institution_kpi`, `mart_*` models). This is what guarantees the Web Team's presentation never disagrees with a direct SQL query against the warehouse — and it's enforced at the database level (`06_Data_Warehouse.md` §5), not just by convention.

## 2. What Is Published (the Contract Surface)

| Mart | Grain | Primary question it answers |
|---|---|---|
| `mart_executive_summary` | Campus-wide, per semester | One-screen institutional health check |
| `mart_college_performance` | Per college, per semester | How is each college trending? |
| `mart_program_performance` | Per program, per semester | How is each program trending, within its college? |
| `mart_institution_kpi` | Per college, per semester | Success Rate composite + all 6 sub-components, per `09_Data_Science.md` |
| `mart_retention_risk` | Per program | Programs with 2+ consecutive semesters of declining retention |
| `fact_forecast` (via `gold`) | Per entity, metric, target semester | Enrollment/graduate/population forecasts with confidence bounds |

Every mart is versioned, tested (`not_null`/`unique`/`relationships` at minimum — `13_Best_Practices.md`), and documented via `dbt docs generate`, which is the literal artifact handed to the Web Team as the interface spec.

## 3. Recommended Consumption Patterns (Guidance, Not a Build Spec)

The sections below describe the kinds of questions each mart is designed to support, based on how administrators actually think about the data (top-line number → which college is driving it → which program within that college). This is offered as **collaboration input for the Web Team**, not a screen-by-screen build plan this repo is delivering — the Web Team decides layout, chart library, navigation, and tooling entirely on their own.

### 3.1 Executive-Level View
**Audience:** university leadership. **Supported by:** `mart_executive_summary`.
- Overall Success Rate (current semester + trend), total enrollment, total graduates, dropout rate.
- Success Rate over time, campus-wide, across all 6 in-scope semesters (`2021-2022, 1st Semester` → `2023-2024, 2nd Semester`).
- Success Rate by college, current semester.

### 3.2 College-Level View
**Supported by:** `mart_college_performance`.
- Enrollment, retention rate, graduation rate, dropout rate, success rate, scoped to one college.
- Success rate by program within the college.
- College success rate over time vs. campus-wide average.

### 3.3 Program-Level View
**Supported by:** `mart_program_performance`.
- Enrollment by year level (`Freshman` through `Super Senior` — all five, not entering students only, per `04_Data_Modeling.md`'s `dim_year_level`).
- Cohort retention curve: entering cohort size → each subsequent year level → graduates.
- Dropout/shifter counts, by semester.

### 3.4 Forecast View
**Supported by:** `fact_forecast` + `mart_institution_kpi`.
- Historical actuals + Prophet forecast with confidence band, selectable metric and entity.
- Forecast accuracy of prior forecasts vs. actuals.
- **A disclosure the Web Team should surface, not bury:** forecasts here are built on only 6 semesters of history (`10_Forecasting.md`), which bounds their confidence more tightly than a longer-running institution's data would.

### 3.5 Institution KPI View
**Supported by:** `mart_institution_kpi`.
- All six Success Rate sub-components shown alongside the composite — directly supporting the transparency principle from `09_Data_Science.md` §5.

## 4. Suggested Chart Types Summary (Non-Binding)

| Question type | Chart |
|---|---|
| "How is X trending over time?" | Line chart |
| "How does X compare across colleges/programs?" | Bar chart |
| "What's the composition of X?" | Stacked bar / area |
| "How does a cohort shrink over time?" | Funnel chart |
| "What's the KPI right now?" | Big number / KPI card with trend arrow |
| "How confident is the forecast?" | Line chart + shaded confidence interval band |

## 5. What This Repo Does Not Decide

Presentation tooling (Superset, Streamlit, a custom web app, anything else), navigation/drill-down implementation, authentication, and UI/UX are entirely the Web Team's decisions. This repo's obligation ends at: a correct, tested, documented `gold`/`marts` schema, and a working `web_service_reader` role. If the Web Team's chosen tool changes tomorrow, nothing in this repo needs to change.

---
*Next: `12_Implementation_Roadmap.md` — the day-by-day 30-day build plan.*