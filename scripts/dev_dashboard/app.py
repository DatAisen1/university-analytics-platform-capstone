"""
scripts/dev_dashboard/app.py

A personal, read-only diagnostic viewer for this repo's own outputs --
NOT the Web Team's dashboard deliverable (see README.md §"Ownership
boundary" and docs/11_Data_Consumption_Contract.md). That document is
explicit: this repo's job ends at a published, tested gold/marts
contract, and 12_Implementation_Roadmap.md records that an earlier,
in-repo Streamlit dashboard was deliberately removed for exactly this
reason. This script does not reopen that decision -- it exists purely
so the person developing this repo can eyeball their own KPI/forecast/
Prophet/trend numbers without hand-writing SQL every time, the same way
a dev might use `psql` or a notebook. It is not wired into
docker-compose.yml, Dagster, or requirements.txt (see
requirements-dashboard.txt next to this file), and nothing else in the
platform depends on it.

Design constraints this file follows on purpose:
  - READ-ONLY. Connects as `dashboard_reader` (pipelines.common.postgres,
    warehouse/ddl/002_grants.sql) -- SELECT on gold + marts, nothing on
    bronze/silver. The exact same role the real Web Team dashboard would
    use, so if this script can see it, the contract is honestly working;
    if it can't, that's a real gap worth knowing about.
  - ZERO business logic. Every number here is read verbatim from a mart
    or a Gold fact table -- no metric is recomputed, re-aggregated, or
    redefined here. That's "one source of truth per metric"
    (README.md, Core Design Philosophy #3) applied to a throwaway
    script, not just the pipeline.
  - Forecast/Prophet grain is PROGRAM, not college (migration
    0013_forecast_program_grain.py). warehouse/ddl/008_forecast_registry.sql
    is a stale pre-migration snapshot that still shows college_key as the
    grain key -- don't trust it; Alembic (migrations/versions/) is the
    sole migration authority per pipelines/common/settings.py's own
    docstring, and 0013 is what actually ran. college_key is kept on
    both tables only as a denormalized rollup column.

Usage:
    pip install -r scripts/dev_dashboard/requirements-dashboard.txt
    streamlit run scripts/dev_dashboard/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from pipelines.common.settings import SettingsError, get_postgres_settings  # noqa: E402
from pipelines.common.postgres import get_role_connection  # noqa: E402

st.set_page_config(page_title="UAP Dev Dashboard", layout="wide")


# ---------------------------------------------------------------------------
# Connection -- one read-only connection per Streamlit session, reusing this
# repo's own settings/role plumbing instead of re-inventing env parsing.
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def get_connection():
    settings = get_postgres_settings(None)
    if not settings.DASHBOARD_READER_PASSWORD:
        raise SettingsError(
            "DASHBOARD_READER_PASSWORD is not set. This dev dashboard connects "
            "as the dashboard_reader role on purpose (read-only, gold+marts "
            "only) -- set it in .env, same as any other service role."
        )
    return get_role_connection(
        "dashboard_reader", settings.DASHBOARD_READER_PASSWORD, env=None
    )


@st.cache_data(show_spinner=False, ttl=60)
def run_query(sql: str, params: Optional[tuple] = None) -> pd.DataFrame:
    conn = get_connection()
    return pd.read_sql(sql, conn, params=params)


def period_label(year: int, semester: int) -> str:
    """Mirrors pipelines/gold/build_dimensions.py's period_label format
    exactly, so forecast (future) periods read identically to historical
    ones on a chart even though they have no gold.dim_academic_period row
    to source a label from (see 008_forecast_registry.sql's module note)."""
    ordinal = "1st" if semester == 1 else "2nd"
    return f"{year}-{year + 1} \u00b7 {ordinal} Semester"


# ---------------------------------------------------------------------------
# App shell
# ---------------------------------------------------------------------------
st.title("University Analytics Platform -- Dev Dashboard")
st.caption(
    "Personal diagnostic view, not the Web Team's deliverable. Read-only "
    "via `dashboard_reader`: gold + marts only. See this file's module "
    "docstring for why it exists and what it deliberately doesn't do."
)

try:
    get_connection()
except SettingsError as exc:
    st.error(str(exc))
    st.stop()
except Exception as exc:  # pragma: no cover -- surfaced to the user, not swallowed
    st.error(
        f"Could not reach Postgres as dashboard_reader: {exc}\n\n"
        "Is the stack up? `docker compose up -d` (or `make up`) from the repo "
        "root, then make sure `warehouse/ddl` / Alembic migrations have run."
    )
    st.stop()

tab_kpi, tab_trend, tab_forecast, tab_prophet = st.tabs(
    ["KPI", "Trend", "Forecast", "Prophet Models"]
)

# ---------------------------------------------------------------------------
# Tab 1 -- KPI: latest-semester snapshot, campus-wide and per college.
# Source: marts.mart_executive_summary, marts.mart_institution_kpi.
# ---------------------------------------------------------------------------
with tab_kpi:
    st.subheader("Institutional KPI -- latest semester")

    exec_df = run_query(
        """
        select academic_year, semester_number, period_label,
               total_enrollment, total_graduates, total_dropouts,
               overall_dropout_rate, overall_retention_rate, overall_institutional_success_index
        from marts.mart_executive_summary
        order by academic_year, semester_number
        """
    )

    if exec_df.empty:
        st.info(
            "marts.mart_executive_summary has no rows yet -- run the dbt "
            "marts build (or the full pipeline) before there's anything to "
            "show here."
        )
    else:
        latest = exec_df.iloc[-1]
        st.markdown(f"**{latest['period_label']}**")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Enrollment", f"{int(latest['total_enrollment']):,}")
        c2.metric("Total Graduates", f"{int(latest['total_graduates']):,}")
        c3.metric("Dropout Rate", f"{latest['overall_dropout_rate']:.1%}")
        c4.metric("Institutional Success Index", f"{latest['overall_institutional_success_index']:.1f}")

        st.divider()
        st.markdown("**Per college -- same semester, all Institutional Success Index sub-components**")

        kpi_df = run_query(
            """
            select college_id, college_name, retention_rate, graduation_rate,
                   dropout_rate, shifter_stability, enrollment_growth, enrollment_volatility,
                   outgoing_shift_count, incoming_shift_count, net_shift_flow,
                   program_completion_momentum, institutional_success_index, enrollment_count,
                   graduation_count
            from marts.mart_institution_kpi
            where academic_year = %(y)s and semester_number = %(s)s
            order by institutional_success_index desc
            """,
            params={"y": int(latest["academic_year"]), "s": int(latest["semester_number"])},
        )
        st.bar_chart(kpi_df.set_index("college_id")["institutional_success_index"])
        st.dataframe(kpi_df, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# Tab 2 -- Trend: Institutional Success Index and enrollment over all
# in-scope semesters, campus-wide vs. a selected college.
# Source: marts.mart_college_performance.
# ---------------------------------------------------------------------------
with tab_trend:
    st.subheader("Trend across all 6 semesters")

    college_df = run_query(
        "select distinct college_id, college_name from marts.mart_college_performance "
        "order by college_id"
    )
    if college_df.empty:
        st.info("marts.mart_college_performance has no rows yet.")
    else:
        selected = st.selectbox(
            "College", college_df["college_id"], format_func=lambda cid: cid
        )
        trend_df = run_query(
            """
            select period_label, academic_year, semester_number, institutional_success_index,
                   campus_avg_institutional_success_index, enrollment_count, retention_rate,
                   dropout_rate
            from marts.mart_college_performance
            where college_id = %(cid)s
            order by academic_year, semester_number
            """,
            params={"cid": selected},
        )
        chart_df = trend_df.set_index("period_label")[
            ["institutional_success_index", "campus_avg_institutional_success_index"]
        ]
        st.line_chart(chart_df)
        st.caption(f"{selected} Institutional Success Index vs. campus-wide average, per semester.")
        st.line_chart(trend_df.set_index("period_label")[["enrollment_count"]])
        st.caption(f"{selected} enrollment, per semester.")
        st.dataframe(trend_df, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# Tab 3 -- Forecast: historical actuals + champion Prophet forecast for a
# selected program/metric, with the 80% confidence band.
# Source: marts.mart_program_performance (actuals) + gold.fact_forecast /
# gold.model_registry (forecast), joined on program_key -- the real grain.
# ---------------------------------------------------------------------------
with tab_forecast:
    st.subheader("Forecast vs. actuals -- program grain")

    program_df = run_query(
        "select distinct program_id, program_name from marts.mart_program_performance "
        "order by program_id"
    )
    if program_df.empty:
        st.info("marts.mart_program_performance has no rows yet.")
    else:
        col_a, col_b = st.columns([2, 1])
        with col_a:
            program_id = st.selectbox("Program", program_df["program_id"])
        with col_b:
            metric = st.selectbox("Metric", ["enrollment_count", "graduation_count"])

        actuals = run_query(
            f"""
            select academic_year, period_label, {metric} as value
            from marts.mart_program_performance
            where program_id = %(pid)s
            order by academic_year
            """,
            params={"pid": program_id},
        )
        actuals["series"] = "actual"

        forecast = run_query(
            """
            select f.target_academic_year, f.target_semester_number,
                   f.yhat, f.yhat_lower, f.yhat_upper, m.is_champion,
                   m.model_version, m.mae, m.beats_baseline
            from gold.fact_forecast f
            join gold.model_registry m on f.model_registry_key = m.model_registry_key
            join gold.dim_program p on f.program_key = p.program_key
            where p.program_id = %(pid)s and f.metric = %(metric)s
              and m.is_champion
            order by f.target_academic_year, f.target_semester_number
            """,
            params={"pid": program_id, "metric": metric},
        )

        if forecast.empty:
            st.warning(
                f"No champion forecast for {program_id} / {metric} yet -- "
                "either the forecast asset hasn't run for this series, or "
                "this program has too little history to train on "
                "(docs/10_Forecasting.md MIN_HISTORY_PERIODS)."
            )
        else:
            forecast["period_label"] = forecast.apply(
                lambda r: period_label(
                    int(r["target_academic_year"]), int(r["target_semester_number"])
                ),
                axis=1,
            )
            forecast_plot = forecast.rename(columns={"yhat": "value"})[
                ["period_label", "value"]
            ]
            forecast_plot["series"] = "forecast"

            combined = pd.concat(
                [actuals[["period_label", "value", "series"]], forecast_plot]
            )
            wide = combined.pivot_table(
                index="period_label", columns="series", values="value"
            )
            st.line_chart(wide)

            band = forecast.set_index("period_label")[["yhat_lower", "yhat_upper", "yhat"]]
            st.caption("Forecast detail -- 80% confidence band (Prophet's yhat_lower/yhat_upper)")
            st.dataframe(band, use_container_width=True)
            st.caption(
                f"Model: {forecast['model_version'].iloc[0]} | "
                f"MAE: {forecast['mae'].iloc[0]:.2f} | "
                f"Beats baseline: {'yes' if forecast['beats_baseline'].iloc[0] else 'no'}"
            )

# ---------------------------------------------------------------------------
# Tab 4 -- Prophet Models: the live champion/candidate registry -- the
# database version of forecasting/artifacts/evaluation_report.md.
# Source: gold.model_registry (every trained candidate, win or lose).
# ---------------------------------------------------------------------------
with tab_prophet:
    st.subheader("Model registry -- champion/candidate history")

    champions_only = st.checkbox("Champions only", value=True)

    registry_sql = """
        select p.program_id, p.program_name, m.metric, m.model_version,
               m.algorithm, m.trained_at, m.mae, m.rmse, m.mape, m.r2,
               m.best_baseline_mae, m.beats_baseline, m.is_champion,
               m.rejected_reason, m.training_record_count
        from gold.model_registry m
        join gold.dim_program p on m.program_key = p.program_key
        {where}
        order by p.program_id, m.metric, m.trained_at desc
    """
    registry_df = run_query(
        registry_sql.format(where="where m.is_champion" if champions_only else "")
    )

    if registry_df.empty:
        st.info(
            "gold.model_registry has no rows yet -- run the training/"
            "evaluation asset first (docs/10_Forecasting.md)."
        )
    else:
        total = registry_df.shape[0] if not champions_only else run_query(
            "select count(*) as n from gold.model_registry"
        )["n"].iloc[0]
        beat = int(registry_df["beats_baseline"].sum())
        st.markdown(
            f"**{beat} / {registry_df.shape[0]}** shown rows beat their best "
            f"baseline{' (champions only)' if champions_only else ''}."
        )
        st.dataframe(registry_df, use_container_width=True, hide_index=True)