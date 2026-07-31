-- Program Dashboard's data source. Unlike mart_institution_kpi (college
-- grain, sourced from the pre-aggregated fact_institution_kpi), this
-- mart aggregates directly from the base staging facts at PROGRAM grain
-- -- the Success Rate composite is explicitly a college-level metric
-- (docs/09_Data_Science.md), so this mart reports the same underlying
-- counts/rates at a finer grain rather than inventing a per-program
-- "success rate" the design doc never defined.
with enrollment_agg as (
    select
        program_key,
        semester_key,
        count(*) as enrollment_count
    from {{ ref('stg_fact_enrollment') }}
    group by program_key, semester_key
),

graduation_agg as (
    select program_key, semester_key, count(*) as graduation_count
    from {{ ref('stg_fact_graduation') }}
    group by program_key, semester_key
),

dropout_agg as (
    select program_key, semester_key, count(*) as dropout_count
    from {{ ref('stg_fact_dropout') }}
    group by program_key, semester_key
)

select
    e.program_key,
    p.program_id,
    p.program_name,
    p.college_id,
    e.semester_key,
    sem.semester_id,
    sem.academic_year,
    e.enrollment_count,
    coalesce(g.graduation_count, 0) as graduation_count,
    coalesce(d.dropout_count, 0) as dropout_count,
    coalesce(d.dropout_count, 0)::float / nullif(e.enrollment_count, 0) as dropout_rate
from enrollment_agg e
join {{ ref('stg_dim_program') }} p on e.program_key = p.program_key
join {{ ref('stg_dim_semester') }} sem on e.semester_key = sem.semester_key
left join graduation_agg g on e.program_key = g.program_key and e.semester_key = g.semester_key
left join dropout_agg d on e.program_key = d.program_key and e.semester_key = d.semester_key
