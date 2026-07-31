select * from {{ source('gold', 'fact_institution_kpi') }}
