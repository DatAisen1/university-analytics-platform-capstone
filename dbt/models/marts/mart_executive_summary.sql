-- Executive Dashboard's data source: one row per semester, campus-wide.
-- Rates are ENROLLMENT-WEIGHTED across colleges, not a simple average of
-- college-level rates -- a simple average would let a small college's
-- rate swing the campus number as much as CICT's, which misrepresents
-- what "campus-wide" should mean.
select
    per.academic_period_key,
    per.period_label,
    per.academic_year,
    per.semester_number,
    sum(kpi.enrollment_count) as total_enrollment,
    sum(kpi.graduation_count) as total_graduates,
    sum(kpi.dropout_count) as total_dropouts,
    sum(kpi.dropout_count)::float / nullif(sum(kpi.enrollment_count), 0) as overall_dropout_rate,
    sum(kpi.enrollment_count * kpi.retention_rate) / nullif(sum(kpi.enrollment_count), 0) as overall_retention_rate,
    sum(kpi.enrollment_count * kpi.success_rate) / nullif(sum(kpi.enrollment_count), 0) as overall_success_rate
from {{ ref('stg_fact_institution_kpi') }} kpi
join {{ ref('stg_dim_academic_period') }} per on kpi.academic_period_key = per.academic_period_key
group by per.academic_period_key, per.period_label, per.academic_year, per.semester_number