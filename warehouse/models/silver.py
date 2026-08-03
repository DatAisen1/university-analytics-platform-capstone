"""
warehouse/models/silver.py

ORM models for the `silver` schema -- cleaned, validated, conformed
entities (see docs/02_System_Architecture.md §3.4). Column definitions
and constraint names match warehouse/ddl/004_silver_star_schema.sql
exactly; see migrations/versions/0004_silver_star_schema.py for the
migration that creates these tables.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean, CheckConstraint, ForeignKey, Numeric, SmallInteger, String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from warehouse.models.base import Base


class College(Base):
    __tablename__ = "college"
    __table_args__ = {"schema": "silver"}

    college_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    college_name: Mapped[str] = mapped_column(String(128), nullable=False)

    programs: Mapped[list["Program"]] = relationship(back_populates="college")


class Program(Base):
    __tablename__ = "program"
    __table_args__ = (
        UniqueConstraint("program_id", name="uq_silver_program_program_id"),
        {"schema": "silver"},
    )

    program_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    program_name: Mapped[str] = mapped_column(String(128), nullable=False)
    college_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("silver.college.college_id", name="fk_silver_program_college"), nullable=False,
    )
    program_level: Mapped[str] = mapped_column(String(16), nullable=False)
    nominal_duration_years: Mapped[float] = mapped_column(Numeric(3, 1), nullable=False)

    college: Mapped["College"] = relationship(back_populates="programs")


class Student(Base):
    __tablename__ = "student"
    __table_args__ = {"schema": "silver"}

    student_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    cohort_academic_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    gender: Mapped[str] = mapped_column(String(16), nullable=False)
    birth_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    home_province: Mapped[str] = mapped_column(String(64), nullable=False)
    admission_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entry_year_level: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    entry_college_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("silver.college.college_id", name="fk_silver_student_college"), nullable=False,
    )
    entry_program_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("silver.program.program_id", name="fk_silver_student_program"), nullable=False,
    )


class Enrollment(Base):
    __tablename__ = "enrollment"
    __table_args__ = (
        UniqueConstraint("student_id", "academic_year", "semester_number", name="uq_silver_enrollment_student_period"),
        CheckConstraint("semester_number IN (1, 2)", name="ck_silver_enrollment_semester_number"),
        {"schema": "silver"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)  # surrogate PK; natural key enforced by the UNIQUE above
    student_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("silver.student.student_id", name="fk_silver_enrollment_student"), nullable=False,
    )
    academic_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    semester_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    college_id: Mapped[str] = mapped_column(String(16), nullable=False)
    program_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("silver.program.program_id", name="fk_silver_enrollment_program"), nullable=False,
    )
    enrollment_status: Mapped[str] = mapped_column(String(32), nullable=False)
    year_level: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    units_enrolled: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_new_enrollee: Mapped[bool] = mapped_column(Boolean, nullable=False)


class Graduation(Base):
    __tablename__ = "graduation"
    __table_args__ = (
        UniqueConstraint("student_id", name="uq_silver_graduation_student"),
        CheckConstraint("semester_number IN (1, 2)", name="ck_silver_graduation_semester_number"),
        {"schema": "silver"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("silver.student.student_id", name="fk_silver_graduation_student"), nullable=False,
    )
    academic_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    semester_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    program_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("silver.program.program_id", name="fk_silver_graduation_program"), nullable=False,
    )
    college_id: Mapped[str] = mapped_column(String(16), nullable=False)
    years_to_complete: Mapped[float] = mapped_column(Numeric(4, 1), nullable=False)


class Dropout(Base):
    __tablename__ = "dropout"
    __table_args__ = (
        UniqueConstraint("student_id", name="uq_silver_dropout_student"),
        CheckConstraint("semester_number IN (1, 2)", name="ck_silver_dropout_semester_number"),
        {"schema": "silver"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("silver.student.student_id", name="fk_silver_dropout_student"), nullable=False,
    )
    academic_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    semester_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    program_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("silver.program.program_id", name="fk_silver_dropout_program"), nullable=False,
    )
    college_id: Mapped[str] = mapped_column(String(16), nullable=False)
    dropout_reason: Mapped[str] = mapped_column(String(64), nullable=False)
    semesters_completed_before_dropout: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class Shifter(Base):
    __tablename__ = "shifter"
    __table_args__ = (
        UniqueConstraint("student_id", "academic_year", "semester_number", name="uq_silver_shifter_student_period"),
        CheckConstraint("semester_number IN (1, 2)", name="ck_silver_shifter_semester_number"),
        {"schema": "silver"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("silver.student.student_id", name="fk_silver_shifter_student"), nullable=False,
    )
    academic_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    semester_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    from_program_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("silver.program.program_id", name="fk_silver_shifter_from_program"), nullable=False,
    )
    to_program_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("silver.program.program_id", name="fk_silver_shifter_to_program"), nullable=False,
    )