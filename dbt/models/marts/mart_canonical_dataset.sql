-- The project's single canonical dataset (see
-- pipelines/common/canonical_schema.py for the authoritative column
-- contract this mart must match). Grain: one row per
-- (academic_year, semester, college, program, gender, year_level).
--
-- freshmen_count is NOT is_new_enrollee alone: is_new_enrollee is true
-- for a Transferee's first semester too (see
-- data_generator/generators/generate_progression.py, tenure_semesters == 1).
-- A "freshman" here means: a student's first-ever semester AND their
-- admission_type is Freshman. Getting this wrong would overstate
-- freshmen by counting incoming transferees.
--
-- applicants / accepted have no source system in this project (no
-- admissions/application event stream exists in Bronze/Silver) -- both
-- are emitted as NULL, never fabricated. See UNSOURCED_METRICS in
-- pipelines/common/canonical_schema.py.
--
-- academic_year/semester output as canonical_schema.py's contract
-- requires ("2021-2022" / "1st Semester"), sourced from
-- dim_academic_period's year_label/semester_label -- NOT the raw
-- academic_year int/semester_number columns. gender and year_level are
-- decoded from dim_student.gender_key / fact_enrollment.year_level_key
-- via dim_gender / dim_year_level -- both dimensions Gold has carried
-- since Task 23/24 but that dbt never sourced, since this mart's
-- previous version read `stu.gender` / `fe.year_level` columns that
-- don't exist on the Gold model at all (a pre-existing break, not
-- something P0.4 introduced).

with enrollment_base as (
    select
        per.year_label as academic_year,
        per.semester_label as semester,
        col.college_name as college,
        prog.program_name as program,
        gen.gender_label as gender,
        {{ year_level_label_sql('yl.year_level', 'prog.nominal_duration_years') }} as year_level,
        fe.student_key,
        fe.college_key,
        fe.program_key,
        fe.academic_period_key,
        fe.is_new_enrollee,
        stu.admission_type
    from {{ ref('stg_fact_enrollment') }} fe
    join {{ ref('stg_dim_academic_period') }} per on fe.academic_period_key = per.academic_period_key
    join {{ ref('stg_dim_college') }} col on fe.college_key = col.college_key
    join {{ ref('stg_dim_program') }} prog on fe.program_key = prog.program_key
    join {{ ref('stg_dim_student') }} stu on fe.student_key = stu.student_key
    join {{ ref('stg_dim_gender') }} gen on stu.gender_key = gen.gender_key
    join {{ ref('stg_dim_year_level') }} yl on fe.year_level_key = yl.year_level_key
),

enrolled_agg as (
    select academic_year, semester, college, program, gender, year_level,
           count(*) as enrolled,
           sum(case when is_new_enrollee and admission_type = 'Freshman' then 1 else 0 end) as freshmen_count
    from enrollment_base
    group by 1, 2, 3, 4, 5, 6
),

graduates_agg as (
    select
        per.year_label as academic_year,
        per.semester_label as semester,
        col.college_name as college,
        prog.program_name as program,
        gen.gender_label as gender,
        count(*) as graduates
    from {{ ref('stg_fact_graduation') }} fg
    join {{ ref('stg_dim_academic_period') }} per on fg.academic_period_key = per.academic_period_key
    join {{ ref('stg_dim_college') }} col on fg.college_key = col.college_key
    join {{ ref('stg_dim_program') }} prog on fg.program_key = prog.program_key
    join {{ ref('stg_dim_student') }} stu on fg.student_key = stu.student_key
    join {{ ref('stg_dim_gender') }} gen on stu.gender_key = gen.gender_key
    group by 1, 2, 3, 4, 5
),

dropouts_agg as (
    select
        per.year_label as academic_year, per.semester_label as semester,
        col.college_name as college, prog.program_name as program,
        gen.gender_label as gender,
        count(*) as dropouts
    from {{ ref('stg_fact_dropout') }} fd
    join {{ ref('stg_dim_academic_period') }} per on fd.academic_period_key = per.academic_period_key
    join {{ ref('stg_dim_college') }} col on fd.college_key = col.college_key
    join {{ ref('stg_dim_program') }} prog on fd.program_key = prog.program_key
    join {{ ref('stg_dim_student') }} stu on fd.student_key = stu.student_key
    join {{ ref('stg_dim_gender') }} gen on stu.gender_key = gen.gender_key
    group by 1, 2, 3, 4, 5
),

shifters_agg as (
    select
        per.year_label as academic_year, per.semester_label as semester,
        col.college_name as college, prog.program_name as program,
        gen.gender_label as gender,
        count(*) as shifters
    from {{ ref('stg_fact_shifter') }} fs
    join {{ ref('stg_dim_academic_period') }} per on fs.academic_period_key = per.academic_period_key
    join {{ ref('stg_dim_program') }} prog on fs.from_program_key = prog.program_key
    join {{ ref('stg_dim_college') }} col on prog.college_key = col.college_key
    join {{ ref('stg_dim_student') }} stu on fs.student_key = stu.student_key
    join {{ ref('stg_dim_gender') }} gen on stu.gender_key = gen.gender_key
    group by 1, 2, 3, 4, 5
)

select
    e.academic_year, e.semester, e.college, e.program, e.gender, e.year_level,
    e.freshmen_count,
    cast(null as integer) as applicants,   -- no source system -- see module note above
    cast(null as integer) as accepted,     -- no source system -- see module note above
    e.enrolled,
    coalesce(g.graduates, 0) as graduates,
    coalesce(d.dropouts, 0) as dropouts,
    coalesce(s.shifters, 0) as shifters
from enrolled_agg e
left join graduates_agg g using (academic_year, semester, college, program, gender)
left join dropouts_agg d using (academic_year, semester, college, program, gender)
left join shifters_agg s using (academic_year, semester, college, program, gender)
order by {{ academic_year_sort_key('e.academic_year') }}, {{ semester_sort_key('e.semester') }}
