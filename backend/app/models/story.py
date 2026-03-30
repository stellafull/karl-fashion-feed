"""Story ORM models."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Story(Base):
    """Immutable same-day story cluster assembled from one or more event frames."""

    __tablename__ = "story"

    story_key: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="general",
    )
    synopsis_zh: Mapped[str] = mapped_column(Text, nullable=False)
    anchor_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    article_membership_json: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    created_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("pipeline_run.run_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    clustering_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
    )
    clustering_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
    )


class StoryFrame(Base):
    """Ordered mapping from stories to their event frames."""

    __tablename__ = "story_frame"

    story_key: Mapped[str] = mapped_column(
        ForeignKey("story.story_key", ondelete="CASCADE"),
        primary_key=True,
    )
    event_frame_id: Mapped[str] = mapped_column(
        ForeignKey("article_event_frame.event_frame_id", ondelete="CASCADE"),
        primary_key=True,
        unique=True,
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class StoryArticle(Base):
    """Ordered mapping from stories to source articles."""

    __tablename__ = "story_article"

    story_key: Mapped[str] = mapped_column(
        ForeignKey("story.story_key", ondelete="CASCADE"),
        primary_key=True,
    )
    article_id: Mapped[str] = mapped_column(
        ForeignKey("article.article_id", ondelete="CASCADE"),
        primary_key=True,
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class StoryFacet(Base):
    """Facet mapping for one story."""

    __tablename__ = "story_facet"

    story_key: Mapped[str] = mapped_column(
        ForeignKey("story.story_key", ondelete="CASCADE"),
        primary_key=True,
    )
    facet: Mapped[str] = mapped_column(String(64), primary_key=True)
