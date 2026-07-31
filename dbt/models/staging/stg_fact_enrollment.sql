select * from {{ source('gold', 'fact_enrollment') }}
