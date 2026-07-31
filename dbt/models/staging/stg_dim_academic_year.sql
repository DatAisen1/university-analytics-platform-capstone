-- Thin staging view over gold.dim_academic_year. No transformation --
-- staging models exist so every downstream mart references source()
-- through here, giving dbt's lineage graph a real staging layer to show,
-- not because this table needs cleaning (Gold already is clean).
select * from {{ source('gold', 'dim_academic_year') }}
