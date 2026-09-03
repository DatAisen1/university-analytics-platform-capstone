-- dbt/macros/trend_metrics.sql
--
-- Shared trend computation (P2 -- Trend Analysis). Written once, applied
-- at both college and program grain by mart_college_trend.sql and
-- mart_program_trend.sql, instead of six window blocks copy-pasted per
-- metric per grain.
--
-- Expects `unpivoted_relation` to already be a CTE of shape:
--   {{ partition_columns }}, academic_period_key, period_ordinal,
--   academic_year, semester_number, metric_name, metric_value
-- (one row per grain + period + metric -- see the calling models for how
-- that shape is produced).
--
-- Ordered by period_ordinal, never academic_period_key -- see
-- pipelines/gold/build_ml_features.py's module docstring, and the bug
-- this exact mistake caused in mart_retention_risk.sql (fixed alongside
-- this change, same task). academic_period_key is a surrogate primary
-- key with no chronological guarantee; period_ordinal is the only
-- column the schema actually promises is chronological
-- (UNIQUE(period_ordinal), warehouse/ddl/003_gold_star_schema.sql).
--
-- Window frame is UNBOUNDED PRECEDING AND CURRENT ROW, not "...AND 1
-- PRECEDING": unlike build_ml_features.py's ML feature tables (which
-- must exclude the current period so a target never leaks into its own
-- predictor), this mart is descriptive -- "the trend as of this
-- period" -- so period t's own value is supposed to be part of period
-- t's trend. Copying the ML pipeline's exclusion frame here would be
-- reusing the wrong half of the pattern.

{% macro trend_metrics(unpivoted_relation, partition_columns) %}

{%- set series_partition = (partition_columns + ['metric_name']) | join(', ') -%}
{%- set yoy_partition = (partition_columns + ['metric_name', 'semester_number']) | join(', ') -%}

select
    u.*,

    -- Semester-over-semester: the immediately preceding period in this
    -- series (grain + metric), chronological order.
    lag(u.metric_value, 1) over (
        partition by {{ series_partition }}
        order by u.period_ordinal
    ) as prior_period_value,

    -- YoY same-semester: the same semester_number one academic_year
    -- earlier. A separate partition/order (by academic_year, within a
    -- fixed semester_number) rather than a period_ordinal offset,
    -- because "2 periods back" and "same semester, last year" are only
    -- the same thing if every year has exactly 2 semesters and none are
    -- ever skipped -- an assumption this macro shouldn't have to make.
    lag(u.metric_value, 1) over (
        partition by {{ yoy_partition }}
        order by u.academic_year
    ) as yoy_same_semester_value,

    -- Linear trend slope (metric units per period), regressed against
    -- period_ordinal so the x-axis is chronological spacing, not the
    -- surrogate key.
    regr_slope(u.metric_value, u.period_ordinal) over (
        partition by {{ series_partition }}
        order by u.period_ordinal
        rows between unbounded preceding and current row
    ) as slope,

    -- Trailing average of the series so far -- used to normalize slope
    -- into a scale-independent "% per period" figure in
    -- trend_classification() below, since a raw slope of 0.5 means very
    -- different things for enrollment_count (headcount) vs.
    -- retention_rate (a 0-1 proportion).
    avg(u.metric_value) over (
        partition by {{ series_partition }}
        order by u.period_ordinal
        rows between unbounded preceding and current row
    ) as trailing_avg_value,

    -- How many periods have been observed for this series as of this
    -- row. Always surfaced alongside trend_classification in the
    -- calling models -- a 3-period trend must never look as confident
    -- as a 12-period one, and this is what makes that checkable rather
    -- than assumed.
    count(*) over (
        partition by {{ series_partition }}
        order by u.period_ordinal
        rows between unbounded preceding and current row
    ) as period_count

from {{ unpivoted_relation }} u

{% endmacro %}


-- dbt/macros/trend_metrics.sql (trend_classification)
--
-- A slope-vs-deadband label, deliberately NOT a hypothesis test --
-- docs/09_Data_Science.md's transparency principle, and this mart's
-- explicit "no significance/hypothesis-testing language at this sample
-- size" requirement. Two failure modes this guards against:
--
--   1. Confidently classifying a 2-point "trend" -- there's no such
--      thing as a trustworthy trend from two data points, so below
--      `trend_min_periods` this returns 'insufficient_history' instead
--      of a directional label, regardless of what the raw slope says.
--   2. Flip-flopping increasing/decreasing every semester on noise near
--      zero slope -- the `trend_deadband_pct` band around zero absorbs
--      that, at the cost of also absorbing genuinely-small real trends
--      (a deliberate, documented tradeoff, not an oversight).
--
-- Both thresholds are dbt vars (dbt_project.yml), not hardcoded, the
-- same way build_kpi.py's WEIGHTS are a module-level dict rather than
-- inlined literals -- so they can be tuned or sensitivity-tested
-- without touching this SQL.

{% macro trend_classification(slope_column, trailing_avg_column, period_count_column) %}
case
    when {{ period_count_column }} < {{ var('trend_min_periods', 3) }}
        then 'insufficient_history'
    when {{ trailing_avg_column }} is null or {{ trailing_avg_column }} = 0
        then 'insufficient_history'
    when ({{ slope_column }} / nullif({{ trailing_avg_column }}, 0)) > {{ var('trend_deadband_pct', 0.02) }}
        then 'increasing'
    when ({{ slope_column }} / nullif({{ trailing_avg_column }}, 0)) < -{{ var('trend_deadband_pct', 0.02) }}
        then 'decreasing'
    else 'stable'
end
{% endmacro %}