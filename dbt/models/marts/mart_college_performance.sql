-- College Dashboard's data source: same grain as mart_institution_kpi,
-- but adds the campus-wide average that semester so a college's number
-- can be read in context ("63.0, vs a campus average of 61.2") rather
-- than in isolation -- directly supporting docs/11_Dashboard.md's
-- "trend line vs campus-wide average" requirement.
with campus_avg as (
    select
        academic_period_key,
        avg(success_rate) as campus_avg_success_rate
    from {{ ref('stg_fact_institution_kpi') }}
    group by academic_period_key
)

select
    kpi.college_key,
    col.college_id,
    col.college_name,
    kpi.academic_period_key,
    per.period_label,
    per.academic_year,
    per.semester_number,
    kpi.enrollment_count,
    kpi.retention_rate,
    kpi.graduation_rate,
    kpi.dropout_rate,
    kpi.success_rate,
    campus_avg.campus_avg_success_rate,
    kpi.success_rate - campus_avg.campus_avg_success_rate as success_rate_vs_campus_avg
from {{ ref('stg_fact_institution_kpi') }} kpi
join {{ ref('stg_dim_college') }} col on kpi.college_key = col.college_key
join {{ ref('stg_dim_academic_period') }} per on kpi.academic_period_key = per.academic_period_key
join campus_avg on kpi.academic_period_key = campus_avg.academic_period_key
