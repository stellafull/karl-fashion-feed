"""Story and pipeline read models."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Story(Base):
    __tablename__ = "story"

    story_key: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    created_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("pipeline_run.run_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    title_zh: Mapped[str] = mapped_column(Text, nullable=False)
    summary_zh: Mapped[str] = mapped_column(Text, nullable=False)
    key_points_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    tags_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hero_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_article_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow_naive)


class StoryArticle(Base):
    __tablename__ = "story_article"

    story_key: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("story.story_key", ondelete="CASCADE"),
        primary_key=True,
    )
    article_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("article.article_id", ondelete="CASCADE"),
        primary_key=True,
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)


class PipelineRun(Base):
    __tablename__ = "pipeline_run"

    run_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    run_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow_naive)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    watermark_ingested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
