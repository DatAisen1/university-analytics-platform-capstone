-- Singular dbt test: fact_institution_kpi's grain is one row per
-- (college_key, semester_key) -- generic dbt tests only support
-- single-column uniqueness, so a composite-key uniqueness check needs a
-- singular test rather than the built-in `unique` test.
select college_key, semester_key, count(*) as row_count
from {{ ref('stg_fact_institution_kpi') }}
group by college_key, semester_key
having count(*) > 1
