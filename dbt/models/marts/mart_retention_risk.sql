-- Decision-support mart (docs/06_Data_Warehouse.md Section 4): flags a
-- program whose retention rate has DECLINED for 2 consecutive
-- semester-over-semester comparisons (strictly: this semester < last
-- semester < the one before that), directly supporting "where should we
-- intervene" -- the administrative question this mart exists to answer.
with program_retention as (
    select
        program_key,
        semester_key,
        avg(is_retained::float) as retention_rate
    from {{ ref('stg_fact_retention') }}
    group by program_key, semester_key
),

with_lag as (
    select
        program_key,
        semester_key,
        retention_rate,
        lag(retention_rate, 1) over (partition by program_key order by semester_key) as retention_rate_prev1,
        lag(retention_rate, 2) over (partition by program_key order by semester_key) as retention_rate_prev2
    from program_retention
)

select
    w.program_key,
    p.program_id,
    p.program_name,
    p.college_id,
    w.semester_key,
    sem.semester_id,
    w.retention_rate,
    w.retention_rate_prev1,
    w.retention_rate_prev2,
    (w.retention_rate < w.retention_rate_prev1 and w.retention_rate_prev1 < w.retention_rate_prev2)
        as is_declining_two_consecutive_semesters
from with_lag w
join {{ ref('stg_dim_program') }} p on w.program_key = p.program_key
join {{ ref('stg_dim_semester') }} sem on w.semester_key = sem.semester_key
where w.retention_rate_prev2 is not null
