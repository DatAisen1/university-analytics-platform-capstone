select * from {{ source('gold', 'fact_dropout') }}
