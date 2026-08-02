# 09 — Data Science: The Institutional Success Rate Model

## 1. Why This Needs a Designed Metric

There is no universal official definition of "institutional success rate" the way there is for GPA or a graduation rate published by a Ministry of Education. Different components (retention, graduation, dropout, etc.) each tell a partial story:

- High graduation rate but also high dropout rate (survivorship bias in the graduation number) → misleading if viewed alone.
- High retention but low graduation → students are "stuck," not progressing.
- Low shifter rate could mean good program fit — or too much bureaucratic friction to switch when a student should.

The Success Rate metric exists to combine these into one number **without hiding the components** — every consumer of the composite score (the Web Team's dashboard included) also gets the inputs, so the metric is explainable, not a black box.

## 2. Component Definitions

For a given `(college, semester)`:

| Component | Formula | What it captures |
|---|---|---|
| Retention Rate (R) | `students continuing into next semester / students enrolled this semester (excluding graduates)` | Are students staying enrolled? |
| Graduation Rate (G) | `graduates this semester / students eligible to graduate (reached nominal duration)` | Are eligible students actually finishing? |
| Dropout Rate (D) | `dropouts this semester / students enrolled this semester` | Attrition — inverse indicator |
| Shifter Stability (Sh) | `1 − (shifters this semester / students enrolled this semester)` | Program fit stability — framed as *stability*, so higher is always "better" |
| Enrollment Stability (E) | `1 − |enrollment_this_semester − enrollment_prior_semester| / enrollment_prior_semester` | Penalizes volatile swings (both drops *and* unsustainable spikes) in enrollment, capped at [0,1] |
| Program Completion Momentum (P) | `students who advanced a year level this semester / students who should have advanced` | Are students progressing on pace, not just "still enrolled" |

Each component is normalized to a `[0, 1]` range before combination, since they are measured in different units and scales and must be comparable before weighting.

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
| Dropout Rate (D) | 0.20 | Direct inverse cost signal; weighted slightly lower than retention/graduation since it's partially redundant with them |
| Program Completion Momentum (P) | 0.15 | Distinguishes "stuck but enrolled" from real progress |
| Shifter Stability (Sh) | 0.05 | Meaningful but lower-stakes — shifting isn't inherently bad |
| Enrollment Stability (E) | 0.05 | Institutional-health signal, lowest direct link to individual student success |

**Why not equal weights (1/6 each)?** Equal weighting would implicitly claim all six factors matter equally, which isn't defensible — dropout and retention are partially measuring the same underlying phenomenon from different angles. The weights above are a **documented, versioned judgment call**, stored as config (`configs/business_rules.yaml`) so the formula can be revised without touching pipeline code, and any historical comparison can note which "success rate formula version" was used. This formula and its weighting are unaffected by the academic-calendar correction — they operate on whatever `(college, semester)` rows Gold produces, regardless of how many semesters exist.

## 4. Worked Example

For a college in one semester: R=0.88, G=0.22 (graduation rate per-semester is naturally lower since only students at nominal-duration-or-beyond are in the eligible pool), D=0.06, Sh=0.97, E=0.95, P=0.80.

```
Success Rate = 0.30(0.22) + 0.25(0.88) + 0.20(1-0.06) + 0.15(0.80) + 0.05(0.97) + 0.05(0.95)
             = 0.066 + 0.22 + 0.188 + 0.12 + 0.0485 + 0.0475
             = 0.690  →  69.0
```
(Scaled ×100 for presentation as a 0–100 index — however the Web Team chooses to display it — mirroring familiar percentage-style KPIs.)

## 5. Design Considerations & Limitations (Disclosed Explicitly)

- **This is a designed index, not a validated psychometric instrument.** It should be presented to stakeholders as "a composite index we defined, here are its components," not as an objective ground truth.
- **Weights are a policy choice.** A university's leadership might legitimately weight graduation higher or retention higher depending on strategic priorities — the config-driven weighting means this is a conversation to have with the metric's config file, not a code change.
- **Cohort size sensitivity**: small programs (e.g., a niche certificate with 20 students/cohort) will show noisier rate swings semester-to-semester purely from small-N variance. With the corrected 6-semester observation window (down from the old, incorrect 8), this sensitivity is somewhat higher than previously documented, since there are fewer observations to smooth over — Gold should publish enrollment counts alongside rates (not just the rate itself) so this isn't misread as volatility in program quality.

## 6. Where This Is Computed

Exactly once, in the Gold layer (`fact_institution_kpi`), by a dbt model (`mart_institution_kpi` reading Gold facts) — never recomputed independently downstream, which is what guarantees every consumer of "the success rate," including the Web Team, is looking at the same number, computed the same way, every time.

## 7. Implementation Notes — KPI Aggregation

> **⚠️ STALE — pending regeneration.** The specific spot-check reported here previously (CICT, semester "2023-1" under the old model, composite `success_rate=63.0`) was measured against the old 8-semester academic-year model and no longer corresponds to a real, current semester label. It must be re-measured against a real semester in the corrected calendar (e.g., `2022-2023, 1st Semester`) once the pipeline is re-run. The formula-level verification in §4 above is unaffected, since it's illustrative rather than tied to a specific real run.

**Module:** `pipelines/gold/build_kpi.py`, run via `python -m pipelines.gold.build_kpi`.

**Design decisions that remain correct and unaffected by the calendar fix:**
- **Graduation Rate's "eligible to graduate" denominator** uses `year_level >= ceil(nominal_duration_years)` as a proxy, since `fact_enrollment` doesn't carry exact semester-tenure as its own column — a conservative approximation, disclosed rather than silently assumed precise.
- **Program Completion Momentum** compares each student's `year_level` this semester against their *own* `year_level` last semester (a self-join on `fact_enrollment`). Students with no prior-semester record (new entrants) are excluded from both numerator and denominator.
- **`fact_shifter` has no `college_key` of its own** — a shift event spans two programs, possibly two colleges. The fix (attribute the shift to the `from_program`'s college, via a join against `dim_program`) is a permanent regression test (`test_build_kpi_shifter_events_attributed_to_from_college`) and does not depend on the academic-calendar model.

**Expected row count under the corrected model:** `fact_institution_kpi` should have **8 colleges × 6 semesters = 48 rows**, not the previously-reported 64.

**Testing:** `tests/unit/test_build_kpi.py` keeps its structure (formula against the worked example, momentum self-join exclusion of new entrants, from-college shifter-attribution regression test); update the full-aggregation fixture to expect 48 rows, not 64, once the 6-semester model is regenerated.

---
*Next: `10_Forecasting.md` — model comparison and selection for enrollment/graduation forecasting.*