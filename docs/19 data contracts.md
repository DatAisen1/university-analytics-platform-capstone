# 19 — Data Contracts

This document is the authoritative reference for **what shape data must
have at each layer**, and **which rules are enforced where**. It is
grounded directly in the pandera schemas, business-rule checks, and
academic-period logic in code — not aspirational design. Where a rule is
enforced by more than one layer for different reasons (shape vs.
relationship), both are listed.

## 1. Schema — Bronze vs. Silver vs. Canonical

Three schema modules exist, each answering a different question:

| Module | Question it answers | Enforcement |
|---|---|---|
| `pipelines/common/schemas.py` | "Is this shaped like the entity at all?" | Column presence/type/range only. **Not** business vocabulary — e.g. `enrollment_status` is any non-empty string, on purpose (see file docstring). `strict=False`. |
| `pipelines/common/silver_schemas.py` | "Did Silver's cleaning stage produce the canonical dtypes/vocabularies it's supposed to?" | Nullable pandas extension dtypes (`Int64`, `string`, `boolean`) matching `clean_entities.py`'s `TARGET_DTYPES`. Controlled vocabularies now enforced (`Male`/`Female`, `Bachelor`/`Certificate`/`Diploma`, etc.), except `enrollment_status`, which stays open (either the 3-value vocabulary or an `UNKNOWN:<raw>` tag pending quarantine). `strict=False`. |
| `pipelines/common/canonical_schema.py` | "Is this THE final analytical dataset every mart/export must conform to?" | `strict=True` — this is the terminal contract. |

Both Bronze and Silver schemas are validated with pandera's `lazy=True`,
meaning **every** violation across the whole DataFrame is collected into one
`SchemaErrors` report rather than stopping at the first bad row.

### 1.1 The 7 Silver entities and their required columns

| Entity | Required columns (beyond audit columns) |
|---|---|
| `college` | `college_id` (unique), `college_name` |
| `program` | `program_id` (unique), `program_name`, `college_id`, `program_level`, `nominal_duration_years` |
| `student` | `student_id` (unique), `cohort_academic_year`, `gender`, `birth_year`, `home_province` (nullable), `admission_type`, `entry_year_level`, `entry_college_id`, `entry_program_id` |
| `enrollment` | `student_id`, `academic_year`, `semester_number`, `college_id`, `program_id`, `enrollment_status`, `year_level`, `units_enrolled`, `is_new_enrollee` |
| `graduation` | `student_id`, `academic_year`, `semester_number`, `program_id`, `college_id`, `years_to_complete` |
| `dropout` | `student_id`, `academic_year`, `semester_number`, `program_id`, `college_id`, `dropout_reason`, `semesters_completed_before_dropout` |
| `shifter` | `student_id`, `academic_year`, `semester_number`, `from_program_id`, `to_program_id` |

### 1.2 The canonical analytical dataset

**Grain:** one row per `(academic_year, semester, college, program, gender,
year_level)`. All metric columns are additive counts at that grain.

**Dimension columns:** `academic_year`, `semester`, `college`, `program`,
`gender`, `year_level`.

**Metric columns:** `freshmen_count`, `applicants`, `accepted`, `enrolled`,
`graduates`, `dropouts`, `shifters`.

**Important gap, by design:** `applicants` and `accepted` have **no source
system** in this project yet — there is no admissions/application event
stream in `data_generator/` or `pipelines/`. The canonical schema declares
both fields **nullable** and requires every builder to emit `NULL`, never
`0` or a fabricated value, until an admissions source actually exists. This
is tracked as a P1 item, not silently worked around.

## 2. Data Types

| Concept | Bronze type | Silver type | Notes |
|---|---|---|---|
| IDs (`student_id`, `college_id`, `program_id`) | `str` | `pandas.StringDtype()` | Non-empty string, `college_id`/`program_id`/`student_id` unique within their own dimension entity |
| `academic_year` | `str` (raw label form) | `Int64Dtype()` (start year, e.g. `2021`) | Bronze carries the source's raw representation; Silver coerces to the integer start-year used everywhere downstream |
| `semester_number` | `int`, `isin([1, 2])` | `Int64Dtype()`, `isin([1, 2])` | See §3 |
| `gender` | `str` (uncontrolled at Bronze) | `Int64`→`string`, `isin(["Male", "Female"])` | Controlled vocabulary only from Silver onward |
| `year_level` | `int`, `>= 1` | `Int64Dtype()`, `>= 1` | See §4 — plausibility is program-relative, not a fixed range |
| `nominal_duration_years` | `float`, in `[0.5, 10]` | same | From `configs/programs.yaml`, one value per `program_id` |
| `units_enrolled`, `semesters_completed_before_dropout`, `entry_year_level` | `int`, `>= 0` / `>= 1` | `Int64Dtype()`, same bound | "Counts are non-negative" (§5) |
| `years_to_complete` | `float`, `> 0` | same | |
| `is_new_enrollee` | `bool` | `BooleanDtype()` | |
| Canonical metric columns (`enrolled`, `graduates`, `dropouts`, `shifters`, `freshmen_count`) | — | `int`, `>= 0` | `applicants`/`accepted` are the sole exception: `float`, nullable (float rather than int specifically so pandas can represent `NaN`) |

## 3. Academic-Year Rules

Source of truth: `pipelines/common/academic_periods.py`.

- **Observed window:** `OBSERVED_START_YEAR = 2021`. The project's Faker
  generator and Silver validation both fix the dataset to 3 academic years —
  `2021-2022`, `2022-2023`, `2023-2024` — i.e. start years
  `{2021, 2022, 2023}` in `academic_periods.py`'s constants, extended to
  `{2021, 2022, 2023, 2024}` in `silver_schemas.py`/`business_rules.py`'s
  `OBSERVED_ACADEMIC_YEARS` to cover the final semester's end year. Any row
  outside this window is quarantined by
  `business_rules.check_academic_year_valid`.
- **Label format:** `"{start_year}-{start_year + 1}"`, e.g. `"2021-2022"`
  (`academic_year_label()`). The canonical schema enforces this exact
  pattern via `Check.str_matches(r"^\d{4}-\d{4}$")`.
- **Chronological ordering is NOT alphabetical for anything beyond simple
  4-digit-year comparison at scale** — `academic_year_categorical_dtype()`
  and `sort_by_academic_period()` exist specifically so every consumer
  sorts/groups by true chronological order via an ordered `CategoricalDtype`
  instead of relying on string sort, which only coincidentally works for
  this project's exact 4-year window.
- **Period ordinal (the single most load-bearing derived value in the ML
  layer):** `academic_period_index(year) * 2 + (0 or 1 for semester)`,
  0-based from `OBSERVED_START_YEAR`. This `period_ordinal` — not the
  `academic_period_key` surrogate key — is the chronological ordering key
  used by every window function, walk-forward fold, and retraining
  comparison in the ML layer (see `20_ML_Assumptions.md`).

## 4. Semester Rules

- Exactly **two** semesters per academic year: `SEMESTER_LABELS = ("1st
  Semester", "2nd Semester")`, `semester_number ∈ {1, 2}`.
- `check_semester_valid` (business_rules.py) and both Bronze/Silver schemas
  independently enforce `semester_number ∈ {1, 2}` — the schema check
  catches a structurally wrong value, the business-rule check catches the
  same thing at the cross-entity validation stage as a second, explicit
  gate.
- **Semester-to-calendar-date mapping** (used to give Prophet a date axis):
  `semester_number == 1 → January 1` of `academic_year`; `semester_number ==
  2 → July 1` of `academic_year`. This is the same convention
  `dim_calendar` establishes (Jan–Jun = semester 1, Jul–Dec = semester 2 of
  the same calendar year) — the ML layer reuses it rather than inventing a
  parallel date convention.
- Ordering between semesters within a year, and across years, must use
  `semester_categorical_dtype()` / `sort_by_academic_period()` — never a
  plain string sort assumption, even though `"1st Semester" <
  "2nd Semester"` happens to also hold alphabetically for these two exact
  strings.

## 5. Year-Level Rules

Source of truth: `pipelines/common/academic_periods.py` +
`pipelines/silver/business_rules.py::check_year_level_valid`.

- **Absolute labels for years 1–4 only:** `1 → Freshman`, `2 → Sophomore`,
  `3 → Junior`, `4 → Senior`. These are fixed regardless of program.
- **"Super Senior" is a derived flag, not a 5th absolute year_level
  bucket.** A student is Super Senior when
  `year_level > ceil(nominal_duration_years)` for **that student's own
  program** (`is_super_senior()`), evaluated against
  `configs/programs.yaml`'s per-program `nominal_duration_years` — never a
  single project-wide cutoff. A 5-year Engineering student at year_level 5
  is on time; a 4-year IT student at year_level 5 is Super Senior. Mixing
  these up was an identified historical bug this rule specifically
  prevents.
- **"Graduate" is explicitly not a year_level.** Graduation is an outcome
  (`fact_graduation` row / `enrollment_status == 'GRADUATED'`), never a
  `year_level` value — conflating the two previously mislabeled active,
  stalled students in long-duration programs as graduates.
- **Plausibility check on ingestion (`check_year_level_valid`):**
  `year_level` must be a positive integer, and must not exceed
  `ceil(nominal_duration_years) + buffer_years` (default `buffer_years =
  2`) for that row's own `program_id`. This buffer exists specifically to
  legitimately cover Super Senior students without hardcoding an arbitrary
  cross-program cap. A row whose `program_id` isn't found in the program
  dimension cannot have its plausibility established and is quarantined.
- **The Super Senior half-rule that lives with the caller, not the
  function:** `is_super_senior()` only answers "has this year_level
  exceeded the program's standard duration?" — the second half ("and the
  student remains enrolled, i.e. not GRADUATED/DROPPED") must be checked
  separately against `enrollment_status` by any caller that needs the full
  rule.

## 6. Business (Cross-Entity / Relational) Rules

Source of truth: `pipelines/silver/business_rules.py`
(`run_business_validation`, the `validation` Dagster asset). These are
distinct from the single-row schema checks above — they require joining
across entities.

1. **`check_program_belongs_to_college`** — every `program.college_id` must
   exist in the Silver college dimension. Applied to the `program`
   dimension itself.
2. **`check_program_college_consistency`** — on fact rows (`enrollment`,
   `graduation`, `dropout`), the row's `(program_id, college_id)` pair must
   agree with that `program_id`'s own registered `college_id` in the
   program dimension — catches denormalization drift, e.g. an enrollment
   row whose `college_id` disagrees with the program dimension.
3. **`check_semester_valid`** — `semester_number` must be a data-driven
   valid value (derived from `SEMESTER_LABELS`, not a hardcoded literal).
4. **`check_academic_year_valid`** — `academic_year` must fall inside the
   observed generation window (§3).
5. **`check_year_level_valid`** — see §5. Applied only to `enrollment` (the
   only fact entity that carries `year_level`).
6. **`check_counts_non_negative`** — generic check for any count-like
   column per entity (`COUNT_COLUMNS`: `units_enrolled`/`year_level` for
   enrollment, `years_to_complete` for graduation,
   `semesters_completed_before_dropout` for dropout,
   `entry_year_level` for student).
7. **`check_admissions_funnel`** (`accepted <= applicants` and
   `enrolled <= accepted`) — **implemented and unit-tested, but not wired
   into `run_business_validation`**, because no entity flowing through
   Bronze/Silver today carries `applicants`/`accepted`/`enrolled_freshmen`
   together (this is the same admissions-funnel gap noted in §1.2). It
   activates with a one-line change to `run_business_validation` the moment
   an `admissions` Silver entity exists.

### 6.1 Validation gate: quarantine escalation

Every business-rule check quarantines violating rows to
`silver_quarantine/<entity>/business_rules.parquet` rather than dropping
them silently. `_enforce_quality_gate()` additionally escalates to a hard,
categorized pipeline failure (`InvalidSchemaError` /
`InvalidAcademicYearError` / `InvalidYearLevelError`, per check) whenever a
**single check's** quarantine rate exceeds `MAX_QUARANTINE_RATE = 0.25`
(25%) of that entity's rows — the threshold at which the data is judged
"structurally broken" rather than exhibiting a normal sprinkling of bad
rows.

## 7. Contract Enforcement Summary (by layer)

| Rule category | Enforced at | Failure behavior |
|---|---|---|
| Column presence/type/range (shape) | Bronze schema | Collected via `lazy=True`; caller-level policy (non-blocking today) |
| Canonical dtype + controlled vocabulary | Silver schema | Same, non-blocking convention per `clean_entities.py` |
| Cross-entity / relational rules | Silver business rules | Quarantine, then hard failure if the quarantine rate for any one check exceeds 25% |
| Final analytical contract | `canonical_schema.py` | `strict=True` — any unexpected/missing column, or a violated `Check`, fails validation outright |