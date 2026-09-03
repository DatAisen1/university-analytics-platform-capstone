-- dbt/models/marts/mart_college_trend.sql
--
-- P2 -- Trend Analysis, college grain. Long/tall: one row per
-- (college, semester, metric), not one row per college-semester with a
-- column per metric -- see dbt/macros/trend_metrics.sql's docstring for
-- why (the window logic is written once and shared with
-- mart_program_trend.sql, which a wide layout would prevent).
--
-- Metric scope is a deliberate P2 decision, not an oversight:
-- mart_institution_kpi exposes ~14 numeric columns, but only these five
-- have a direct stakeholder question behind them (docs/09's
-- transparency principle again -- don't trend a number nobody asked
-- about just because it happens to exist). Hand-unpivoted via UNION ALL
-- rather than dbt_utils.unpivot, so the metric list stays explicit here
-- and doesn't silently grow if a column is later added to
-- mart_institution_kpi.
--
-- Every delta/slope/classification column is computed by the shared
-- trend_metrics() / trend_classification() macros in
-- dbt/macros/trend_metrics.sql -- see that file for the
-- leakage-safe-ordering and classification rationale; this model only
-- supplies the grain and metric list.

with source_metrics as (

    select college_key, academic_period_key, 'enrollment_count' as metric_name, enrollment_count::numeric as metric_value
    from {{ ref('mart_institution_kpi') }}

    union all

    select college_key, academic_period_key, 'retention_rate', retention_rate::numeric
    from {{ ref('mart_institution_kpi') }}

    union all

    select college_key, academic_period_key, 'graduation_rate', graduation_rate::numeric
    from {{ ref('mart_institution_kpi') }}

    union all

    select college_key, academic_period_key, 'dropout_rate', dropout_rate::numeric
    from {{ ref('mart_institution_kpi') }}

    union all

    select college_key, academic_period_key, 'institutional_success_index', institutional_success_index::numeric
    from {{ ref('mart_institution_kpi') }}

),

-- Period attributes (period_ordinal, semester_number, academic_year)
-- are read fresh from the dimension here, not carried through from
-- mart_institution_kpi -- the one place ordering is governed, per the
-- same "read a dimension's ordinal attribute from the one place it's
-- governed" pattern build_kpi.py already uses for year_level.
unpivoted as (

    select
        sm.college_key,
        sm.academic_period_key,
        per.period_ordinal,
        per.period_label,
        per.academic_year,
        per.semester_number,
        sm.metric_name,
        sm.metric_value
    from source_metrics sm
    join {{ ref('stg_dim_academic_period') }} per on sm.academic_period_key = per.academic_period_key

),

trended as (
    {{ trend_metrics('unpivoted', ['college_key']) }}
)

select
    t.college_key,
    col.college_id,
    col.college_name,
    t.academic_period_key,
    t.period_label,
    t.academic_year,
    t.semester_number,
    t.metric_name,
    t.metric_value,
    t.prior_period_value,
    t.metric_value - t.prior_period_value as semester_over_semester_abs_delta,
    (t.metric_value - t.prior_period_value) / nullif(t.prior_period_value, 0) as semester_over_semester_pct_delta,
    t.yoy_same_semester_value,
    t.metric_value - t.yoy_same_semester_value as yoy_abs_delta,
    (t.metric_value - t.yoy_same_semester_value) / nullif(t.yoy_same_semester_value, 0) as yoy_pct_delta,
    t.slope,
    t.period_count,
    {{ trend_classification('t.slope', 't.trailing_avg_value', 't.period_count') }} as trend_classification
from trended t
join {{ ref('stg_dim_college') }} col on t.college_key = col.college_key
order by t.college_key, t.metric_name, t.period_ordinal