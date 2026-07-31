-- One row per (college, semester): all six Success Rate sub-components
-- alongside the composite, with human-readable college/semester labels
-- joined in. Per docs/09_Data_Science.md's transparency principle, the
-- composite score is never surfaced without its inputs -- this mart is
-- the direct, queryable expression of that principle, not just a doc
-- statement.
select
    kpi.college_key,
    col.college_id,
    col.college_name,
    kpi.semester_key,
    sem.semester_id,
    sem.academic_year,
    sem.semester_number,
    kpi.enrollment_count,
    kpi.graduation_count,
    kpi.dropout_count,
    kpi.shifter_count,
    kpi.retention_rate,
    kpi.graduation_rate,
    kpi.dropout_rate,
    kpi.shifter_stability,
    kpi.enrollment_stability,
    kpi.program_completion_momentum,
    kpi.success_rate
from {{ ref('stg_fact_institution_kpi') }} kpi
join {{ ref('stg_dim_college') }} col on kpi.college_key = col.college_key
join {{ ref('stg_dim_semester') }} sem on kpi.semester_key = sem.semester_key
