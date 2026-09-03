-- One row per (college, semester): all Institutional Success Index
-- sub-components alongside the composite, with human-readable
-- college/semester labels joined in. Per docs/09_Data_Science.md's
-- transparency principle, the composite score is never surfaced
-- without its inputs -- this mart is the direct, queryable expression
-- of that principle, not just a doc statement.
--
-- P2 KPI Redesign (migrations/versions/0017_kpi_redesign.py): the old
-- single `shifter_count` and `enrollment_stability` columns are now
-- three and two columns respectively, and the composite is renamed.
-- See pipelines/gold/build_kpi.py's module docstring for the full
-- rationale.
select
    kpi.college_key,
    col.college_id,
    col.college_name,
    kpi.academic_period_key,
    per.period_label,
    per.academic_year,
    per.semester_number,
    kpi.enrollment_count,
    kpi.graduation_count,
    kpi.dropout_count,
    kpi.outgoing_shift_count,
    kpi.incoming_shift_count,
    kpi.net_shift_flow,
    kpi.retention_rate,
    kpi.graduation_rate,
    kpi.dropout_rate,
    kpi.shifter_stability,
    kpi.enrollment_growth,
    kpi.enrollment_volatility,
    kpi.program_completion_momentum,
    kpi.institutional_success_index
from {{ ref('stg_fact_institution_kpi') }} kpi
join {{ ref('stg_dim_college') }} col on kpi.college_key = col.college_key
join {{ ref('stg_dim_academic_period') }} per on kpi.academic_period_key = per.academic_period_key