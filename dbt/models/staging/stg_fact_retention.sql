select * from {{ source('gold', 'fact_retention') }}
