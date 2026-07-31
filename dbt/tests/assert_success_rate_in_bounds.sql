-- Singular dbt test: fails if the query returns any rows.
-- The Success Rate composite (docs/09_Data_Science.md) is defined to
-- land in [0, 100] by construction (each weighted component is itself
-- bounded [0,1] and the weights sum to 1.0) -- this test exists to catch
-- a REGRESSION in that guarantee (e.g. a future change to the weights or
-- component formulas that breaks the bound), not because it's expected
-- to ever fail on correct data.
select *
from {{ ref('stg_fact_institution_kpi') }}
where success_rate < 0 or success_rate > 100
