{% macro is_super_senior(year_level_col, nominal_duration_years_col) %}
    {{ year_level_col }} > ceil({{ nominal_duration_years_col }})
{% endmacro %}

{% macro year_level_label_sql(year_level_col, nominal_duration_years_col) %}
    case
        when {{ year_level_col }} <= 1 then 'Freshman'
        when {{ year_level_col }} = 2 then 'Sophomore'
        when {{ year_level_col }} = 3 then 'Junior'
        when {{ year_level_col }} = 4 then 'Senior'
        when {{ is_super_senior(year_level_col, nominal_duration_years_col) }} then 'Super Senior'
        else 'Senior'  -- on-time extra year in a 5+-year program
    end
{% endmacro %}