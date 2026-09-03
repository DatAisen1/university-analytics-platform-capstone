-- Singular dbt test: fails if the query returns any rows.
--
-- The whole point of period_count existing (P2 -- Trend Analysis) is
-- that a low-period series should never carry a confident-looking
-- 'increasing'/'decreasing'/'stable' label -- this test catches a
-- REGRESSION of that guarantee (e.g. a future edit to
-- trend_classification() that reorders the CASE branches and lets
-- slope arithmetic run before the period_count check), not because it's
-- expected to ever fail on correct data.

select college_key as grain_key, academic_period_key, metric_name, period_count, trend_classification
from {{ ref('mart_college_trend') }}
where period_count < {{ var('trend_min_periods', 3) }}
  and trend_classification != 'insufficient_history'

union all

select program_key as grain_key, academic_period_key, metric_name, period_count, trend_classification
from {{ ref('mart_program_trend') }}
where period_count < {{ var('trend_min_periods', 3) }}
  and trend_classification != 'insufficient_history'
