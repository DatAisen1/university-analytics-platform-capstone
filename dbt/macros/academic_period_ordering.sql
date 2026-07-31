{% macro academic_year_sort_key(column) %}
    cast(split_part({{ column }}, '-', 1) as integer)
{% endmacro %}

{% macro semester_sort_key(column) %}
    case {{ column }}
        when '1st Semester' then 1
        when '2nd Semester' then 2
        else null  -- fail loudly downstream rather than silently mis-sort
    end
{% endmacro %}