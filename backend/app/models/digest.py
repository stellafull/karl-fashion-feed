"""Digest ORM models."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Digest(Base):
    """Public digest artifact generated from stories and articles."""

    __tablename__ = "digest"

    digest_key: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    facet: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title_zh: Mapped[str] = mapped_column(Text, nullable=False)
    dek_zh: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    hero_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_article_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_names_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("pipeline_run.run_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    generation_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
    )
    generation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
    )

    @property
    def selected_source_article_ids(self) -> tuple[str, ...] | None:
        """Writer-selected source article IDs used to build digest memberships."""
        raw_value = self.__dict__.get("_selected_source_article_ids")
        if raw_value is None:
            return None
        return tuple(raw_value)

    @selected_source_article_ids.setter
    def selected_source_article_ids(self, article_ids: tuple[str, ...] | list[str] | None) -> None:
        if article_ids is None:
            self.__dict__.pop("_selected_source_article_ids", None)
            return
        self.__dict__["_selected_source_article_ids"] = tuple(article_ids)


class DigestStory(Base):
    """Ordered mapping from digests to stories.

    A story may appear in multiple digests for the same business day.
    """

    __tablename__ = "digest_story"

    digest_key: Mapped[str] = mapped_column(
        ForeignKey("digest.digest_key", ondelete="CASCADE"),
        primary_key=True,
    )
    story_key: Mapped[str] = mapped_column(
        ForeignKey("story.story_key", ondelete="CASCADE"),
        primary_key=True,
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DigestArticle(Base):
    """Ordered mapping from digests to supporting articles."""

    __tablename__ = "digest_article"

    digest_key: Mapped[str] = mapped_column(
        ForeignKey("digest.digest_key", ondelete="CASCADE"),
        primary_key=True,
    )
    article_id: Mapped[str] = mapped_column(
        ForeignKey("article.article_id", ondelete="CASCADE"),
        primary_key=True,
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
