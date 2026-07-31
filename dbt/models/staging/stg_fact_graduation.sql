select * from {{ source('gold', 'fact_graduation') }}
