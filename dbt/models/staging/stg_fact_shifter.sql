select * from {{ source('gold', 'fact_shifter') }}
