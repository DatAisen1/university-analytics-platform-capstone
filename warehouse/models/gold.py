"""
warehouse/models/gold.py

ORM models for the `gold` schema -- the star schema, ML feature tables,
and the forecast/model registry. Matches warehouse/ddl/003, 005, 007,
008, 009 (ported to migrations/versions/0003.. through 0009..).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, ForeignKey, Integer,
    Numeric, SmallInteger, String, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from warehouse.models.base import Base


class DimAcademicPeriod(Base):
    __tablename__ = "dim_academic_period"
    __table_args__ = (
        UniqueConstraint("academic_year", "semester_number", name="uq_gold_dim_academic_period_year_semester"),
        UniqueConstraint("period_ordinal", name="uq_gold_dim_academic_period_ordinal"),
        CheckConstraint("semester_number IN (1, 2)", name="ck_gold_dim_academic_period_semester_number"),
        {"schema": "gold"},
    )

    academic_period_key: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    academic_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    semester_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    year_label: Mapped[str] = mapped_column(String(16), nullable=False)
    semester_label: Mapped[str] = mapped_column(String(16), nullable=False)
    period_label: Mapped[str] = mapped_column(String(32), nullable=False)
    period_ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class DimCalendar(Base):
    __tablename__ = "dim_calendar"
    __table_args__ = (
        CheckConstraint("quarter BETWEEN 1 AND 4", name="ck_gold_dim_calendar_quarter"),
        CheckConstraint("month BETWEEN 1 AND 12", name="ck_gold_dim_calendar_month"),
        CheckConstraint("day BETWEEN 1 AND 31", name="ck_gold_dim_calendar_day"),
        {"schema": "gold"},
    )

    date_key: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_date: Mapped[datetime] = mapped_column(Date, nullable=False, unique=True)
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    quarter: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    month: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_semester_start: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_semester_end: Mapped[bool] = mapped_column(Boolean, nullable=False)
    academic_period_key: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("gold.dim_academic_period.academic_period_key", name="fk_gold_dim_calendar_academic_period"),
        nullable=False, index=True,
    )


class DimYearLevel(Base):
    __tablename__ = "dim_year_level"
    __table_args__ = {"schema": "gold"}

    year_level_key: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    year_level: Mapped[int] = mapped_column(SmallInteger, nullable=False, unique=True)
    year_level_label: Mapped[str] = mapped_column(String(32), nullable=False)


class DimGender(Base):
    __tablename__ = "dim_gender"
    __table_args__ = {"schema": "gold"}

    gender_key: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    gender_code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    gender_label: Mapped[str] = mapped_column(String(16), nullable=False)


class DimCollege(Base):
    __tablename__ = "dim_college"
    __table_args__ = (
        UniqueConstraint("college_id", name="uq_gold_dim_college_college_id"),
        {"schema": "gold"},
    )

    college_key: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    college_id: Mapped[str] = mapped_column(String(16), nullable=False)
    college_name: Mapped[str] = mapped_column(String(128), nullable=False)


class DimProgram(Base):
    __tablename__ = "dim_program"
    __table_args__ = (
        UniqueConstraint("program_id", name="uq_gold_dim_program_program_id"),
        {"schema": "gold"},
    )

    program_key: Mapped[int] = mapped_column(Integer, primary_key=True)
    program_id: Mapped[str] = mapped_column(String(32), nullable=False)
    program_name: Mapped[str] = mapped_column(String(128), nullable=False)
    college_id: Mapped[str] = mapped_column(String(16), nullable=False)
    program_level: Mapped[str] = mapped_column(String(16), nullable=False)
    nominal_duration_years: Mapped[float] = mapped_column(Numeric(3, 1), nullable=False)
    college_key: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("gold.dim_college.college_key", name="fk_gold_dim_program_college"),
        nullable=False, index=True,
    )


class DimStudent(Base):
    __tablename__ = "dim_student"
    __table_args__ = {"schema": "gold"}
    # NOTE: ux_dim_student_one_current (partial UNIQUE INDEX WHERE _is_current)
    # is NOT expressible as a plain SQLAlchemy UniqueConstraint -- it's created
    # directly in migrations/versions/0003_gold_star_schema.py via op.execute(),
    # same as the original DDL. Documented here so a reader of this model
    # doesn't assume the ORM alone enforces "one current row per student."

    student_key: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    gender_key: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("gold.dim_gender.gender_key", name="fk_gold_dim_student_gender"), nullable=False,
    )
    birth_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    home_province: Mapped[str] = mapped_column(String(64), nullable=False)
    admission_type: Mapped[str] = mapped_column(String(16), nullable=False)
    college_key: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("gold.dim_college.college_key", name="fk_gold_dim_student_college"), nullable=False,
    )
    program_key: Mapped[int] = mapped_column(
        Integer, ForeignKey("gold.dim_program.program_key", name="fk_gold_dim_student_program"), nullable=False,
    )
    _valid_from_period_key: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("gold.dim_academic_period.academic_period_key", name="fk_gold_dim_student_valid_from"),
        nullable=False,
    )
    _valid_to_period_key: Mapped[int | None] = mapped_column(
        SmallInteger, ForeignKey("gold.dim_academic_period.academic_period_key", name="fk_gold_dim_student_valid_to"),
        nullable=True,
    )
    _is_current: Mapped[bool] = mapped_column(Boolean, nullable=False)


def _fact_columns():
    """Shared column shape for the five grain-per-student-period fact
    tables below -- kept as a helper rather than a mixin class, since
    the ORM's __table_args__ (constraint names) differ per table and a
    mixin would obscure that each FK/constraint name is deliberately
    unique per table, not inherited."""
    raise NotImplementedError("documentation helper only -- see each Fact* class")


class FactEnrollment(Base):
    __tablename__ = "fact_enrollment"
    __table_args__ = (
        UniqueConstraint("student_key", "academic_period_key", name="uq_gold_fact_enrollment_student_period"),
        {"schema": "gold"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_key: Mapped[int] = mapped_column(Integer, ForeignKey("gold.dim_student.student_key", name="fk_gold_fact_enrollment_student"), nullable=False, index=True)
    program_key: Mapped[int] = mapped_column(Integer, ForeignKey("gold.dim_program.program_key", name="fk_gold_fact_enrollment_program"), nullable=False)
    college_key: Mapped[int] = mapped_column(SmallInteger, ForeignKey("gold.dim_college.college_key", name="fk_gold_fact_enrollment_college"), nullable=False)
    academic_period_key: Mapped[int] = mapped_column(SmallInteger, ForeignKey("gold.dim_academic_period.academic_period_key", name="fk_gold_fact_enrollment_period"), nullable=False)
    enrollment_status: Mapped[str] = mapped_column(String(16), nullable=False)
    year_level_key: Mapped[int] = mapped_column(SmallInteger, ForeignKey("gold.dim_year_level.year_level_key", name="fk_gold_fact_enrollment_year_level"), nullable=False)
    units_enrolled: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_new_enrollee: Mapped[bool] = mapped_column(Boolean, nullable=False)


class FactGraduation(Base):
    __tablename__ = "fact_graduation"
    __table_args__ = (
        UniqueConstraint("student_key", name="uq_gold_fact_graduation_student"),
        {"schema": "gold"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_key: Mapped[int] = mapped_column(Integer, ForeignKey("gold.dim_student.student_key", name="fk_gold_fact_graduation_student"), nullable=False)
    program_key: Mapped[int] = mapped_column(Integer, ForeignKey("gold.dim_program.program_key", name="fk_gold_fact_graduation_program"), nullable=False)
    college_key: Mapped[int] = mapped_column(SmallInteger, ForeignKey("gold.dim_college.college_key", name="fk_gold_fact_graduation_college"), nullable=False)
    academic_period_key: Mapped[int] = mapped_column(SmallInteger, ForeignKey("gold.dim_academic_period.academic_period_key", name="fk_gold_fact_graduation_period"), nullable=False)
    years_to_complete: Mapped[float] = mapped_column(Numeric(4, 1), nullable=False)


class FactDropout(Base):
    __tablename__ = "fact_dropout"
    __table_args__ = (
        UniqueConstraint("student_key", name="uq_gold_fact_dropout_student"),
        {"schema": "gold"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_key: Mapped[int] = mapped_column(Integer, ForeignKey("gold.dim_student.student_key", name="fk_gold_fact_dropout_student"), nullable=False)
    program_key: Mapped[int] = mapped_column(Integer, ForeignKey("gold.dim_program.program_key", name="fk_gold_fact_dropout_program"), nullable=False)
    college_key: Mapped[int] = mapped_column(SmallInteger, ForeignKey("gold.dim_college.college_key", name="fk_gold_fact_dropout_college"), nullable=False)
    academic_period_key: Mapped[int] = mapped_column(SmallInteger, ForeignKey("gold.dim_academic_period.academic_period_key", name="fk_gold_fact_dropout_period"), nullable=False)
    dropout_reason: Mapped[str] = mapped_column(String(32), nullable=False)
    semesters_completed_before_dropout: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class FactShifter(Base):
    __tablename__ = "fact_shifter"
    __table_args__ = (
        UniqueConstraint("student_key", "academic_period_key", name="uq_gold_fact_shifter_student_period"),
        {"schema": "gold"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_key: Mapped[int] = mapped_column(Integer, ForeignKey("gold.dim_student.student_key", name="fk_gold_fact_shifter_student"), nullable=False)
    from_program_key: Mapped[int] = mapped_column(Integer, ForeignKey("gold.dim_program.program_key", name="fk_gold_fact_shifter_from_program"), nullable=False)
    to_program_key: Mapped[int] = mapped_column(Integer, ForeignKey("gold.dim_program.program_key", name="fk_gold_fact_shifter_to_program"), nullable=False)
    academic_period_key: Mapped[int] = mapped_column(SmallInteger, ForeignKey("gold.dim_academic_period.academic_period_key", name="fk_gold_fact_shifter_period"), nullable=False)


class FactRetention(Base):
    __tablename__ = "fact_retention"
    __table_args__ = (
        UniqueConstraint("student_key", "academic_period_key", name="uq_gold_fact_retention_student_period"),
        CheckConstraint("is_retained IN (0, 1)", name="ck_gold_fact_retention_is_retained"),
        {"schema": "gold"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_key: Mapped[int] = mapped_column(Integer, ForeignKey("gold.dim_student.student_key", name="fk_gold_fact_retention_student"), nullable=False)
    program_key: Mapped[int] = mapped_column(Integer, ForeignKey("gold.dim_program.program_key", name="fk_gold_fact_retention_program"), nullable=False)
    college_key: Mapped[int] = mapped_column(SmallInteger, ForeignKey("gold.dim_college.college_key", name="fk_gold_fact_retention_college"), nullable=False)
    academic_period_key: Mapped[int] = mapped_column(SmallInteger, ForeignKey("gold.dim_academic_period.academic_period_key", name="fk_gold_fact_retention_period"), nullable=False)
    is_retained: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class FactInstitutionKpi(Base):
    __tablename__ = "fact_institution_kpi"
    __table_args__ = {"schema": "gold"}

    college_key: Mapped[int] = mapped_column(SmallInteger, ForeignKey("gold.dim_college.college_key", name="fk_gold_fact_kpi_college"), primary_key=True)
    academic_period_key: Mapped[int] = mapped_column(SmallInteger, ForeignKey("gold.dim_academic_period.academic_period_key", name="fk_gold_fact_kpi_period"), primary_key=True)
    enrollment_count: Mapped[int] = mapped_column(Integer, nullable=False)
    graduation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    dropout_count: Mapped[int] = mapped_column(Integer, nullable=False)
    shifter_count: Mapped[int] = mapped_column(Integer, nullable=False)
    retention_rate: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    graduation_rate: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    dropout_rate: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    shifter_stability: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    enrollment_stability: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    program_completion_momentum: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    success_rate: Mapped[float] = mapped_column(Numeric(5, 1), nullable=False)


class MLProgramForecastFeatures(Base):
    __tablename__ = "ml_program_forecast_features"
    __table_args__ = {"schema": "gold"}

    college_key: Mapped[int] = mapped_column(SmallInteger, ForeignKey("gold.dim_college.college_key", name="fk_gold_ml_program_features_college"), primary_key=True)
    program_key: Mapped[int] = mapped_column(Integer, ForeignKey("gold.dim_program.program_key", name="fk_gold_ml_program_features_program"), primary_key=True)
    academic_period_key: Mapped[int] = mapped_column(SmallInteger, ForeignKey("gold.dim_academic_period.academic_period_key", name="fk_gold_ml_program_features_period"), primary_key=True)
    academic_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    semester_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    period_ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    enrollment_count: Mapped[int] = mapped_column(Integer, nullable=False)
    enrollment_count_lag_1: Mapped[int | None] = mapped_column(Integer)
    enrollment_count_lag_2: Mapped[int | None] = mapped_column(Integer)
    enrollment_count_rolling_avg_2: Mapped[float | None] = mapped_column(Numeric(10, 2))
    enrollment_count_historical_avg: Mapped[float | None] = mapped_column(Numeric(10, 2))
    enrollment_count_trend: Mapped[float | None] = mapped_column(Numeric(12, 4))
    enrollment_count_seasonality: Mapped[float | None] = mapped_column(Numeric(10, 2))
    enrollment_count_growth: Mapped[float | None] = mapped_column(Numeric(10, 4))
    graduation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    graduation_count_lag_1: Mapped[int | None] = mapped_column(Integer)
    graduation_count_lag_2: Mapped[int | None] = mapped_column(Integer)
    graduation_count_rolling_avg_2: Mapped[float | None] = mapped_column(Numeric(10, 2))
    graduation_count_historical_avg: Mapped[float | None] = mapped_column(Numeric(10, 2))
    graduation_count_trend: Mapped[float | None] = mapped_column(Numeric(12, 4))
    graduation_count_seasonality: Mapped[float | None] = mapped_column(Numeric(10, 2))
    graduation_count_growth: Mapped[float | None] = mapped_column(Numeric(10, 4))


class MLEnrollmentFeaturesByYearLevel(Base):
    __tablename__ = "ml_enrollment_features_by_year_level"
    __table_args__ = {"schema": "gold"}

    college_key: Mapped[int] = mapped_column(SmallInteger, ForeignKey("gold.dim_college.college_key", name="fk_gold_ml_enrollment_yl_college"), primary_key=True)
    program_key: Mapped[int] = mapped_column(Integer, ForeignKey("gold.dim_program.program_key", name="fk_gold_ml_enrollment_yl_program"), primary_key=True)
    year_level_key: Mapped[int] = mapped_column(SmallInteger, ForeignKey("gold.dim_year_level.year_level_key", name="fk_gold_ml_enrollment_yl_year_level"), primary_key=True)
    academic_period_key: Mapped[int] = mapped_column(SmallInteger, ForeignKey("gold.dim_academic_period.academic_period_key", name="fk_gold_ml_enrollment_yl_period"), primary_key=True)
    academic_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    semester_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    period_ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    enrollment_count: Mapped[int] = mapped_column(Integer, nullable=False)
    enrollment_count_lag_1: Mapped[int | None] = mapped_column(Integer)
    enrollment_count_lag_2: Mapped[int | None] = mapped_column(Integer)
    enrollment_count_rolling_avg_2: Mapped[float | None] = mapped_column(Numeric(10, 2))
    enrollment_count_historical_avg: Mapped[float | None] = mapped_column(Numeric(10, 2))
    enrollment_count_trend: Mapped[float | None] = mapped_column(Numeric(12, 4))
    enrollment_count_seasonality: Mapped[float | None] = mapped_column(Numeric(10, 2))
    enrollment_count_growth: Mapped[float | None] = mapped_column(Numeric(10, 4))


class ModelRegistry(Base):
    __tablename__ = "model_registry"
    __table_args__ = (
        UniqueConstraint("college_key", "metric", "model_version", name="uq_gold_model_registry_college_metric_version"),
        CheckConstraint("metric IN ('enrollment_count', 'graduation_count')", name="ck_gold_model_registry_metric"),
        {"schema": "gold"},
    )

    model_registry_key: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    college_key: Mapped[int] = mapped_column(SmallInteger, ForeignKey("gold.dim_college.college_key", name="fk_gold_model_registry_college"), nullable=False, index=True)
    metric: Mapped[str] = mapped_column(String(32), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    trained_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    mae: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    rmse: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    mape: Mapped[float | None] = mapped_column(Numeric(14, 4))
    r2: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False)
    best_baseline_mae: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    beats_baseline: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_champion: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    promoted_at: Mapped[datetime | None] = mapped_column()
    rejected_reason: Mapped[str | None] = mapped_column(String(256))
    artifact_path: Mapped[str] = mapped_column(String(256), nullable=False)
    # Task 40-42 versioning fields (0009_model_versioning_fields.py)
    algorithm: Mapped[str | None] = mapped_column(String(32))
    training_data_start_period_ordinal: Mapped[int | None] = mapped_column(SmallInteger)
    training_data_end_period_ordinal: Mapped[int | None] = mapped_column(SmallInteger)
    training_record_count: Mapped[int | None] = mapped_column(Integer)
    # NOTE: ux_model_registry_one_champion (partial UNIQUE INDEX WHERE
    # is_champion) is created directly in the migration, same reasoning
    # as DimStudent._is_current above -- not expressible as a plain
    # SQLAlchemy UniqueConstraint.


class FactForecast(Base):
    __tablename__ = "fact_forecast"
    __table_args__ = (
        UniqueConstraint("college_key", "metric", "target_period_ordinal", "model_version", name="uq_gold_fact_forecast_college_metric_period_version"),
        CheckConstraint("metric IN ('enrollment_count', 'graduation_count')", name="ck_gold_fact_forecast_metric"),
        CheckConstraint("target_semester_number IN (1, 2)", name="ck_gold_fact_forecast_semester_number"),
        CheckConstraint("yhat >= 0", name="ck_gold_fact_forecast_yhat_nonnegative"),
        {"schema": "gold"},
    )

    fact_forecast_key: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    college_key: Mapped[int] = mapped_column(SmallInteger, ForeignKey("gold.dim_college.college_key", name="fk_gold_fact_forecast_college"), nullable=False)
    metric: Mapped[str] = mapped_column(String(32), nullable=False)
    target_academic_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    target_semester_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    target_period_ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False, index=True)
    model_registry_key: Mapped[int] = mapped_column(BigInteger, ForeignKey("gold.model_registry.model_registry_key", name="fk_gold_fact_forecast_model_registry"), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    yhat: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    yhat_lower: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    yhat_upper: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())