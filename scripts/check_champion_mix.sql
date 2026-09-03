-- scripts/check_champion_mix.sql
--
-- Read-only. The `forecast` Dagster asset already calls deploy_forecasts()
-- as part of the full pipeline job, which already wrote real champions
-- into gold.model_registry for all 74 series (see orchestration/assets.py's
-- forecast asset, models/forecasting/deploy_forecast.py). This just reads
-- that back -- no retraining, no writes, safe to run any time.
--
-- Usage:
--   docker exec -it uap_postgres psql -U uap_admin -d university_analytics -f scripts/check_champion_mix.sql
-- or paste the query body directly at an interactive psql> prompt.

select
    metric,
    algorithm,
    count(*) as series_won,
    round(100.0 * count(*) / sum(count(*)) over (partition by metric), 1) as pct_of_metric
from gold.model_registry
where is_champion is true
group by metric, algorithm
order by metric, series_won desc;