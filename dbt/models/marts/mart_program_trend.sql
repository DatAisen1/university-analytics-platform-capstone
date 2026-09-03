-- dbt/models/marts/mart_program_trend.sql
--
-- P2 -- Trend Analysis, program grain. Long/tall, same shape and same
-- shared trend_metrics()/trend_classification() macros as
-- mart_college_trend.sql (dbt/macros/trend_metrics.sql) -- see that
-- model and macro file for the full rationale; this file only differs
-- in grain (program_key) and metric list.
--
-- Metric scope: enrollment_count, graduation_count, and dropout_rate --
-- the three program-grain metrics mart_program_performance already
-- computes and that map to real stakeholder questions ("is this
-- program growing", "is completion moving", "is attrition worsening").
-- Deliberately excludes dropout_count on its own: dropout_rate is the
-- governed, denominator-normalized version of the same signal
-- (mart_program_performance.sql), and trending both would just be
-- trending the same underlying movement twice under different names.

with source_metrics as (

    select program_key, academic_period_key, 'enrollment_count' as metric_name, enrollment_count::numeric as metric_value
    from {{ ref('mart_program_performance') }}

    union all

    select program_key, academic_period_key, 'graduation_count', graduation_count::numeric
    from {{ ref('mart_program_performance') }}

    union all

    select program_key, academic_period_key, 'dropout_rate', dropout_rate::numeric
    from {{ ref('mart_program_performance') }}

),

unpivoted as (

    select
        sm.program_key,
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
    {{ trend_metrics('unpivoted', ['program_key']) }}
)

select
    t.program_key,
    prog.program_id,
    prog.program_name,
    prog.college_id,
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
join {{ ref('stg_dim_program') }} prog on t.program_key = prog.program_key
order by t.program_key, t.metric_name, t.period_ordinal