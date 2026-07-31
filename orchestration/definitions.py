"""
orchestration/definitions.py

The Dagster entry point: wires orchestration/assets.py's 9 assets into a
single job (materialize everything, Bronze through dbt marts + ML
features) and a schedule matching a real registrar's actual cadence.

On the schedule's cadence: a real university delivers batch extracts
roughly twice a year (once per semester), not on any shorter cycle --
there's no "hourly" or "daily" version of a semester. The cron expression
below (midnight on Jan 1 and Jul 1) is illustrative of that real-world
cadence for a production deployment; it is not something this capstone
project runs unattended over months to observe. Manually triggering the
job (via the Dagster UI's "Materialize all" or `dagster job execute`) is
how this gets exercised in practice, here and in any reasonable capstone
timeline.

Run the Dagster UI (webserver) via:
    dagster dev -f orchestration/definitions.py
"""

from dagster import Definitions, ScheduleDefinition, define_asset_job

from orchestration.assets import (
    bronze_layer,
    dbt_staging_and_marts,
    gold_dimensions,
    gold_facts,
    gold_in_postgres,
    gold_kpi,
    ml_forecast_features,
    silver_cleaned,
    silver_validated,
)

all_assets = [
    bronze_layer,
    silver_cleaned,
    silver_validated,
    gold_dimensions,
    gold_facts,
    gold_kpi,
    gold_in_postgres,
    ml_forecast_features,
    dbt_staging_and_marts,
]

full_pipeline_job = define_asset_job(
    name="full_pipeline_job",
    selection=all_assets,
    description="Materializes the entire Bronze -> Silver -> Gold -> dbt pipeline, in dependency order.",
)

semester_schedule = ScheduleDefinition(
    job=full_pipeline_job,
    # Illustrative of a real semester-batch cadence (see module docstring)
    # -- Jan 1 and Jul 1, matching this project's own Jan-Jun / Jul-Dec
    # semester boundary convention (docs/04_Data_Modeling.md's dim_calendar).
    cron_schedule="0 0 1 1,7 *",
    name="per_semester_pipeline_run",
)

defs = Definitions(
    assets=all_assets,
    jobs=[full_pipeline_job],
    schedules=[semester_schedule],
)
