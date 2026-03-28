"""Runtime ORM models for digest pipeline execution."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base

ASIA_SHANGHAI = ZoneInfo("Asia/Shanghai")
ARTICLE_STAGE_MAX_ATTEMPTS = 3
BATCH_STAGE_MAX_ATTEMPTS = 3
SOURCE_RUN_MAX_ATTEMPTS = 3
DEFAULT_STALE_STATE_TIMEOUT = timedelta(minutes=30)


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def coerce_utc_naive(value: datetime) -> datetime:
    """Normalize an input datetime to a naive UTC timestamp."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def business_day_for_runtime(now: datetime) -> date:
    """Resolve the active business day using Asia/Shanghai local date."""
    normalized = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    return normalized.astimezone(ASIA_SHANGHAI).date()


def utc_bounds_for_business_day(business_day: date) -> tuple[datetime, datetime]:
    """Return the naive UTC window covering one Asia/Shanghai business day."""
    start_local = datetime.combine(business_day, time.min, tzinfo=ASIA_SHANGHAI)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(UTC).replace(tzinfo=None),
        end_local.astimezone(UTC).replace(tzinfo=None),
    )


class PipelineRun(Base):
    """Batch execution state for one business-date digest run."""

    __tablename__ = "pipeline_run"
    __table_args__ = (
        Index(
            "uq_pipeline_run_business_date_run_type",
            "business_date",
            "run_type",
            unique=True,
        ),
    )

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
    strict_story_token: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
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
    digest_token: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
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
