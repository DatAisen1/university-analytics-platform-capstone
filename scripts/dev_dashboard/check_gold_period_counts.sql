-- scripts/check_gold_period_counts.sql
--
-- Isolates whether the marts.mart_executive_summary staleness (6 vs 10
-- distinct academic_period_key) is a genuinely stale gold.fact_institution_kpi
-- table, or just a dbt run that hasn't been re-triggered against an
-- already-correct gold layer.
--
-- fact_institution_kpi and fact_enrollment/fact_graduation are built
-- together in the same Dagster `gold` asset call (build_all_facts() +
-- build_kpi() -- see orchestration/assets.py). ml_program_forecast_features
-- is built downstream, from those same fact_enrollment/fact_graduation
-- tables (see pipelines/gold/build_ml_features.py's module docstring).
-- So if these don't all agree, gold itself is inconsistent -- not just
-- the dbt layer.
--
-- Usage:
--   psql -h localhost -U uap_admin -d university_analytics -f scripts/check_gold_period_counts.sql

select 'dim_academic_period' as table_name,
       count(*) as total_rows,
       count(distinct academic_period_key) as distinct_periods
from gold.dim_academic_period

union all

select 'fact_institution_kpi',
       count(*),
       count(distinct academic_period_key)
from gold.fact_institution_kpi

union all

select 'fact_enrollment',
       count(*),
       count(distinct academic_period_key)
from gold.fact_enrollment

union all

select 'fact_graduation',
       count(*),
       count(distinct academic_period_key)
from gold.fact_graduation

union all

select 'ml_program_forecast_features',
       count(*),
       count(distinct academic_period_key)
from gold.ml_program_forecast_features

order by table_name;