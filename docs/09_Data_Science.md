# 09 — Data Science: The Institutional Success Index Model

> **Renamed in the P2 KPI Redesign** (`migrations/versions/0017_kpi_redesign.py`, `pipelines/gold/build_kpi.py`). This doc used to call the composite the "Success Rate"; the DB column, ORM, dbt marts, and dashboard now all call it `institutional_success_index`, and this doc is updated to match — a name only used in prose while the schema said something else would recreate the exact doc/schema mismatch already found elsewhere in this repo (`dim_calendar`, `configs/business_rules.yaml`). The redesign also split two components that were discarding direction information — see §2 and §7.

## 1. Why This Needs a Designed Metric

There is no universal official definition of "institutional success" the way there is for GPA or a graduation rate published by a Ministry of Education. Different components (retention, graduation, dropout, etc.) each tell a partial story:

- High graduation rate but also high dropout rate (survivorship bias in the graduation number) → misleading if viewed alone.
- High retention but low graduation → students are "stuck," not progressing.
- Low shifter rate could mean good program fit — or too much bureaucratic friction to switch when a student should.

The Institutional Success Index exists to combine these into one number **without hiding the components** — every consumer of the composite score (the Web Team's dashboard included) also gets the inputs, so the metric is explainable, not a black box.

## 2. Component Definitions

For a given `(college, semester)`:

| Component | Formula | What it captures |
|---|---|---|
| Retention Rate (R) | `students continuing into next semester / students enrolled this semester (excluding graduates)` | Are students staying enrolled? |
| Graduation Rate (G) | `graduates this semester / students eligible to graduate (reached nominal duration)` | Are eligible students actually finishing? |
| Dropout Rate (D) | `dropouts this semester / students enrolled this semester` | Attrition — inverse indicator |
| Shifter Stability (Sh) | `1 − (outgoing cross-college shifters this semester / students enrolled this semester)` | Program fit stability — framed as *stability*, so higher is always "better". Scoped to cross-college moves only: a same-college program switch (e.g. BS CS → BS IT, both under CICT) doesn't change that college's population and is excluded, not counted as a shift out |
| Enrollment Volatility (V) | `|enrollment_this_semester − enrollment_prior_semester| / enrollment_prior_semester`, clipped to `[0,1]` | Magnitude of swing in enrollment — feeds the composite inverted (`1 − V`, "lower magnitude is better"), since a large swing threatens semester-to-semester capacity planning regardless of direction |
| Program Completion Momentum (P) | `students who advanced a year level this semester / students who should have advanced` | Are students progressing on pace, not just "still enrolled" |

Each component that feeds the composite is normalized to a `[0, 1]` range before combination, since they are measured in different units and scales and must be comparable before weighting.

**Two informational-only columns are published alongside the composite but do not feed it**, because they have no universal "good" direction at this grain — it's the college's job to interpret them, not this pipeline's:
- **Enrollment Growth**: the *signed* period-over-period % change in enrollment (unclipped). Enrollment Volatility above is this same delta's *magnitude*, clipped and inverted into the composite; Growth keeps the sign so a dashboard can distinguish "shrank 20%" from "grew 20%," which Volatility alone can't.
- **Net Shift Flow**: `incoming_shift_count − outgoing_shift_count` (both cross-college only). Net inflow isn't inherently a success signal — Shifter Stability above (built from `outgoing_shift_count` only) is what feeds the composite.

## 3. Weighted Composite Formula

```
Institutional Success Index = (w_R · R) + (w_G · G) + (w_D · (1 − D)) + (w_Sh · Sh) + (w_V · (1 − V)) + (w_P · P)

where  w_R + w_G + w_D + w_Sh + w_V + w_P = 1
```

### Suggested Weights (Rationale-Driven, Not Arbitrary)

| Component | Weight | Rationale |
|---|---|---|
| Graduation Rate (G) | 0.30 | The clearest terminal success signal — the ultimate institutional objective |
| Retention Rate (R) | 0.25 | Strongest leading indicator of eventual graduation |
| Dropout Rate (D) | 0.20 | Direct inverse cost signal; weighted slightly lower than retention/graduation since it's partially redundant with them |
| Program Completion Momentum (P) | 0.15 | Distinguishes "stuck but enrolled" from real progress |
| Shifter Stability (Sh) | 0.05 | Meaningful but lower-stakes — shifting isn't inherently bad |
| Enrollment Volatility (V) | 0.05 | Institutional-health signal, lowest direct link to individual student success |

**Why not equal weights (1/6 each)?** Equal weighting would implicitly claim all six factors matter equally, which isn't defensible — dropout and retention are partially measuring the same underlying phenomenon from different angles. The weights above are a **documented, versioned judgment call**, stored as config (`configs/business_rules.yaml`) and reused, not re-derived, by `scripts/kpi_weight_sensitivity.py` (§7) so a sensitivity analysis can never silently drift from the production formula. Any historical comparison can note which "Institutional Success Index formula version" was used. This formula and its weighting are unaffected by the academic-calendar correction — they operate on whatever `(college, semester)` rows Gold produces, regardless of how many semesters exist.

## 4. Worked Example

For a college in one semester: R=0.88, G=0.22 (graduation rate per-semester is naturally lower since only students at nominal-duration-or-beyond are in the eligible pool), D=0.06, Sh=0.97, V=0.05, P=0.80.

```
Institutional Success Index = 0.30(0.22) + 0.25(0.88) + 0.20(1-0.06) + 0.15(0.80) + 0.05(0.97) + 0.05(1-0.05)
                             = 0.066 + 0.22 + 0.188 + 0.12 + 0.0485 + 0.0475
                             = 0.690  →  69.0
```
(Scaled ×100 for presentation as a 0–100 index — however the Web Team chooses to display it — mirroring familiar percentage-style KPIs. Note V=0.05 here is the same underlying swing magnitude as the pre-redesign doc's `E=0.95` — `(1 − V)` in the formula produces the identical `0.0475` term, since Enrollment Volatility is Enrollment Stability's replacement, not a different measurement.)

## 5. Design Considerations & Limitations (Disclosed Explicitly)

- **This is a designed index, not a validated psychometric instrument.** It should be presented to stakeholders as "a composite index we defined, here are its components," not as an objective ground truth.
- **Weights are a policy choice.** A university's leadership might legitimately weight graduation higher or retention higher depending on strategic priorities — the config-driven weighting means this is a conversation to have with the metric's config file, not a code change.
- **Cohort size sensitivity**: small programs (e.g., a niche certificate with 20 students/cohort) will show noisier rate swings semester-to-semester purely from small-N variance. With the corrected 6-semester observation window (down from the old, incorrect 8), this sensitivity is somewhat higher than previously documented, since there are fewer observations to smooth over — Gold should publish enrollment counts alongside rates (not just the rate itself) so this isn't misread as volatility in program quality.
- **Enrollment Growth and Net Shift Flow are informational-only** (§2) — a dashboard consumer who only looks at the composite will not see whether a college's volatility came from growth or decline, or whether its shift stability came from balanced flow or one-directional attrition. Both raw signed columns are published specifically so that distinction isn't lost.

## 6. Where This Is Computed

Exactly once, in the Gold layer (`fact_institution_kpi`), by a dbt model (`mart_institution_kpi` reading Gold facts) — never recomputed independently downstream, which is what guarantees every consumer of "the Institutional Success Index," including the Web Team, is looking at the same number, computed the same way, every time.

## 7. Implementation Notes — KPI Aggregation

> **⚠️ STALE — pending regeneration.** The specific spot-check reported here previously (CICT, semester "2023-1" under the old model, composite `institutional_success_index=63.0`) was measured against the old 8-semester academic-year model and no longer corresponds to a real, current semester label. It must be re-measured against a real semester in the corrected calendar (e.g., `2022-2023, 1st Semester`) once the pipeline is re-run. The formula-level verification in §4 above is unaffected, since it's illustrative rather than tied to a specific real run.

**Module:** `pipelines/gold/build_kpi.py`, run via `python -m pipelines.gold.build_kpi`.

**Design decisions that remain correct and unaffected by the calendar fix:**
- **Graduation Rate's "eligible to graduate" denominator** uses `year_level >= ceil(nominal_duration_years)` as a proxy, since `fact_enrollment` doesn't carry exact semester-tenure as its own column — a conservative approximation, disclosed rather than silently assumed precise.
- **Program Completion Momentum** compares each student's `year_level` this semester against their *own* `year_level` last semester (a self-join on `fact_enrollment`). Students with no prior-semester record (new entrants) are excluded from both numerator and denominator.
- **`fact_shifter` has no `college_key` of its own** — a shift event spans two programs, possibly two colleges. Both endpoints are recovered via a join against `dim_program` (`from_college_key`, `to_college_key`). Since the P2 KPI Redesign (§2), only shifts where these two differ count toward `outgoing_shift_count`/`incoming_shift_count` — a same-college program switch changes no college's population and is excluded from both. This scoping is covered by regression tests for both the outgoing and incoming side, plus a same-college-switch exclusion test (`tests/unit/test_build_kpi.py`), and does not depend on the academic-calendar model.

**Expected row count under the corrected model:** `fact_institution_kpi` should have **8 colleges × 6 semesters = 48 rows**, not the previously-reported 64.

**Testing:** `tests/unit/test_build_kpi.py` keeps its structure (formula against the worked example, momentum self-join exclusion of new entrants, cross-college shifter-attribution regression tests) plus the P2 additions (signed-vs-magnitude growth/volatility, same-college switches excluded, the `weights`-override path); update the full-aggregation fixture to expect 48 rows, not 64, once the 6-semester model is regenerated.

**Weight sensitivity:** `scripts/kpi_weight_sensitivity.py` reruns `compute_success_rate` (`pipelines/gold/build_kpi.py`) under four weight vectors — current, equal, graduation-heavy, retention-heavy — against already-computed component values, and reports which colleges/periods change rank or flip order under a different policy choice. It calls the same function production uses via its `weights` override parameter, so the sensitivity numbers can never drift from what's actually deployed.

---
*Next: `10_Forecasting.md` — model comparison and selection for enrollment/graduation forecasting.*