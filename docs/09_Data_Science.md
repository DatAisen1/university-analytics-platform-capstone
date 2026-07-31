# 09 — Data Science: The Institutional Success Rate Model

## 1. Why This Needs a Designed Metric

There is no universal official definition of "institutional success rate" the way there is for GPA or a graduation rate published by a Ministry of Education. Different components (retention, graduation, dropout, etc.) each tell a partial story:

- High graduation rate but also high dropout rate (survivorship bias in the graduation number) → misleading if viewed alone.
- High retention but low graduation → students are "stuck," not progressing.
- Low shifter rate could mean good program fit — or too much bureaucratic friction to switch when a student should.

The Success Rate metric exists to combine these into one number **without hiding the components** — every dashboard showing the composite score also shows its inputs, so the metric is explainable, not a black box.

## 2. Component Definitions

For a given `(college, semester)`:

| Component | Formula | What it captures |
|---|---|---|
| Retention Rate (R) | `students continuing into next semester / students enrolled this semester (excluding graduates)` | Are students staying enrolled? |
| Graduation Rate (G) | `graduates this semester / students eligible to graduate (reached nominal duration)` | Are eligible students actually finishing? |
| Dropout Rate (D) | `dropouts this semester / students enrolled this semester` | Attrition — inverse indicator |
| Shifter Stability (Sh) | `1 − (shifters this semester / students enrolled this semester)` | Program fit stability — framed as *stability*, so higher is always "better," consistent with the other components |
| Enrollment Stability (E) | `1 − |enrollment_this_semester − enrollment_prior_semester| / enrollment_prior_semester` | Penalizes volatile swings (both drops *and* unsustainable spikes) in enrollment, capped at [0,1] |
| Program Completion Momentum (P) | `students who advanced a year level this semester / students who should have advanced` | Are students progressing on pace, not just "still enrolled" |

Each component is normalized to a `[0, 1]` range before combination, since they are measured in different units and scales (rates vs. ratios vs. counts) and must be comparable before weighting.

## 3. Weighted Composite Formula

```
Success Rate = (w_R · R) + (w_G · G) + (w_D · (1 − D)) + (w_Sh · Sh) + (w_E · E) + (w_P · P)

where  w_R + w_G + w_D + w_Sh + w_E + w_P = 1
```

### Suggested Weights (Rationale-Driven, Not Arbitrary)

| Component | Weight | Rationale |
|---|---|---|
| Graduation Rate (G) | 0.30 | The clearest terminal success signal — the ultimate institutional objective |
| Retention Rate (R) | 0.25 | Strongest leading indicator of eventual graduation |
| Dropout Rate (D) | 0.20 | Direct inverse cost signal; weighted slightly lower than retention/graduation since it's partially redundant with them (a student who doesn't drop out either retains or graduates) |
| Program Completion Momentum (P) | 0.15 | Distinguishes "stuck but enrolled" from real progress |
| Shifter Stability (Sh) | 0.05 | Meaningful but lower-stakes than the above — shifting isn't inherently bad (right-fit correction) |
| Enrollment Stability (E) | 0.05 | Institutional-health signal, lowest direct link to individual student success |

**Why not equal weights (1/6 each)?** Equal weighting would implicitly claim all six factors matter equally to "institutional success," which isn't defensible — dropout and retention are partially measuring the same underlying phenomenon from different angles, so equal weight would double-count that signal relative to, say, enrollment stability, which measures something structurally different. The weights above are a **documented, versioned judgment call** — this is disclosed explicitly (not hidden), and stored as a config (`configs/business_rules.yaml`) so the formula can be revised in a future iteration without touching pipeline code, and any historical comparison can note which "success rate formula version" was used.

## 4. Worked Example

For a college in one semester: R=0.88, G=0.22 (graduation rate per-semester is naturally lower since only students at nominal-duration-or-beyond are in the eligible pool), D=0.06, Sh=0.97, E=0.95, P=0.80.

```
Success Rate = 0.30(0.22) + 0.25(0.88) + 0.20(1-0.06) + 0.15(0.80) + 0.05(0.97) + 0.05(0.95)
             = 0.066 + 0.22 + 0.188 + 0.12 + 0.0485 + 0.0475
             = 0.690  →  69.0
```
(Scaled ×100 for dashboard display as a 0–100 index, mirroring familiar percentage-style KPIs.)

## 5. Design Considerations & Limitations (Disclosed Explicitly)

- **This is a designed index, not a validated psychometric instrument.** It should be presented to stakeholders as "a composite index we defined, here are its components," not as an objective ground truth — this framing itself is a data governance practice (metric provenance and disclosure).
- **Weights are a policy choice.** A university's leadership might legitimately weight graduation higher or retention higher depending on strategic priorities — the config-driven weighting means this is a conversation to have with the metric's config file, not a code change.
- **Cohort size sensitivity**: small programs (e.g., a niche certificate with 20 students/cohort) will show noisier rate swings semester-to-semester purely from small-N variance — the dashboard should show enrollment counts alongside rates so this isn't misread as volatility in program quality.

## 6. Where This Is Computed

Exactly once, in the Gold layer (`fact_institution_kpi`), by a dbt model (`mart_institution_kpi` reading Gold facts) — never recomputed independently by the dashboard or by ad hoc analyst queries, which is what guarantees every consumer of "the success rate" is looking at the same number, computed the same way, every time.

## 7. Implementation Notes — KPI Aggregation (Day 14)

**Module:** `pipelines/gold/build_kpi.py`, run via `python -m pipelines.gold.build_kpi`. (The dbt model referenced above lands in Week 3 once Postgres is live — this is the pandas/DuckDB implementation the dbt model will eventually wrap.)

**Formula verified against this doc's own worked example, not just internally consistent:** `compute_success_rate(R=0.88, G=0.22, D=0.06, Sh=0.97, E=0.95, P=0.80)` returns exactly `69.0`, matching Section 4 above precisely.

**Two derivation choices the abstract design left open, made concrete and disclosed here:**
- **Graduation Rate's "eligible to graduate" denominator** uses `year_level >= ceil(nominal_duration_years)` as a proxy, since `fact_enrollment` doesn't carry exact semester-tenure as its own column. This slightly *under*-counts eligibility whenever a student has stalled (year_level lags tenure, never leads it) — a conservative approximation, disclosed rather than silently assumed precise.
- **Program Completion Momentum** compares each student's `year_level` this semester against their *own* `year_level` last semester (a self-join on `fact_enrollment`, the same pattern `fact_retention` already uses). Students with no prior-semester record (new entrants) are excluded from both numerator and denominator — "did they advance" isn't a meaningful question for someone in their first semester, so they're excluded, not penalized.

**A real bug, found and fixed while building this:** `fact_shifter` has no `college_key` of its own — a shift event spans two programs (from/to), possibly two different colleges, so there's no single unambiguous college for the fact row. The first implementation crashed outright (`KeyError: 'college_key'`) trying to group by a column that was never there. **Fix:** attribute a shift event to the `from_program`'s college — it's that college's population being depleted, which is what `shifter_stability` is meant to measure — resolved via a join against `dim_program`. This is now a permanent regression test (`test_build_kpi_shifter_events_attributed_to_from_college`).

**Spot-checked against real data — the actual Day 14 validation checklist item, not just "the code runs":** College of ICT (CICT), semester 2023-1: `retention_rate=0.912`, `graduation_rate=0.0` (no 4-year program had reached eligibility yet at this point in the observed window — consistent with the graduation-timing limitation documented in `08_Faker_Data_Generator.md`), `dropout_rate=0.043`, `shifter_stability=0.994`, `enrollment_stability=0.451`, `program_completion_momentum=0.923` → composite `success_rate=63.0`. Manually recomputing the formula from these exact stored component values reproduces `63.0` precisely — the table isn't just internally consistent, it's independently reproducible from its own disclosed inputs, which is the entire point of storing components alongside the composite (Section 5's transparency principle).

**An observed pattern worth naming, not hiding:** `enrollment_stability` dips noticeably at each academic year's first semester (e.g., 2023-1 above, at 0.451) across colleges. This is expected, not a bug — new cohorts only ever enter in semester 1 of a given academic year (see `08_Faker_Data_Generator.md`), so college populations naturally jump at that boundary while dropouts/graduates trickle out more evenly. A real dashboard should annotate this pattern rather than let it read as "enrollment volatility" the institution should worry about.

**Testing:** `tests/unit/test_build_kpi.py` — 11 tests: the formula against the documented worked example plus edge cases (perfect/worst scores), the momentum self-join's exclusion of new entrants, full-aggregation tests against small fixtures (one row per college×semester, correct eligible-denominator graduation rate, every composite score bounded to [0, 100]), and the from-college shifter-attribution fix as an explicit regression test.

---
*Next: `10_Forecasting.md` — model comparison and selection for enrollment/graduation forecasting.*
