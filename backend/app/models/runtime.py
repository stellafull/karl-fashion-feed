"""Runtime ORM models for digest pipeline execution."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class PipelineRun(Base):
    """Batch execution state for one business-date digest run."""

    __tablename__ = "pipeline_run"

    run_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    run_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="digest_daily",
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        index=True,
    )
    strict_story_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
    )
    strict_story_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    strict_story_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    strict_story_updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
    )
    digest_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
    )
    digest_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    digest_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    digest_updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class SourceRunState(Base):
    """Per-source collection state inside one pipeline run."""

    __tablename__ = "source_run_state"

    run_id: Mapped[str] = mapped_column(
        ForeignKey("pipeline_run.run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_name: Mapped[str] = mapped_column(String(120), primary_key=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
    )
    discovered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inserted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
