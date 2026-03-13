"""Immutable story aggregation models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, event, inspect, select
from sqlalchemy.orm import Mapped, Session as ORMSession, mapped_column

from backend.app.core.database import Base
from backend.app.models.common import JSON_PAYLOAD_TYPE, utcnow


class Story(Base):
    __tablename__ = "story"

    story_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    key_points: Mapped[list] = mapped_column(JSON_PAYLOAD_TYPE, nullable=False, default=list)
    topic_tags: Mapped[list] = mapped_column(JSON_PAYLOAD_TYPE, nullable=False, default=list)
    category_id: Mapped[str | None] = mapped_column(String(64))
    category_name: Mapped[str | None] = mapped_column(String(128))
    cover_image_url: Mapped[str | None] = mapped_column(Text)
    representative_article_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("document.article_id"),
    )
    rank_score: Mapped[float | None] = mapped_column(Numeric(10, 4))
    importance_score: Mapped[float | None] = mapped_column(Numeric(10, 4))
    freshness_score: Mapped[float | None] = mapped_column(Numeric(10, 4))
    article_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    last_aggregated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    newest_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON_PAYLOAD_TYPE, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class StoryArticle(Base):
    __tablename__ = "story_article"
    __table_args__ = (
        Index("ix_story_article_story_key_sort_order", "story_key", "sort_order"),
    )

    story_key: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("story.story_key"),
        primary_key=True,
    )
    article_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("document.article_id"),
        primary_key=True,
    )
    member_score: Mapped[float | None] = mapped_column(Numeric(10, 4))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_representative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


@event.listens_for(ORMSession, "before_flush")
def _sync_story_representatives(
    session: ORMSession,
    flush_context: object,
    instances: object,
) -> None:
    affected_story_keys = _collect_affected_story_keys(session)
    if not affected_story_keys:
        return

    for story_key in affected_story_keys:
        story = _resolve_story(session, story_key)
        if story is None:
            continue

        story_articles = _resolve_story_articles(session, story_key)
        representative_article_id = story.representative_article_id
        if representative_article_id is None:
            for story_article in story_articles.values():
                story_article.is_representative = False
            continue

        if representative_article_id not in story_articles:
            raise ValueError(
                "Story representative_article_id must match a story_article member. "
                f"story_key={story_key!r} representative_article_id={representative_article_id!r}"
            )

        # Treat representative_article_id as the source of truth and normalize flags to match.
        for article_id, story_article in story_articles.items():
            story_article.is_representative = article_id == representative_article_id


def _collect_affected_story_keys(session: ORMSession) -> set[str]:
    story_keys: set[str] = set()
    for instance in _iter_session_objects(session):
        if isinstance(instance, Story):
            story_keys.add(instance.story_key)
        elif isinstance(instance, StoryArticle):
            story_keys.add(instance.story_key)
    return story_keys


def _resolve_story(session: ORMSession, story_key: str) -> Story | None:
    for instance in _iter_session_objects(session):
        if not isinstance(instance, Story) or instance.story_key != story_key:
            continue
        if inspect(instance).deleted:
            return None
        return instance

    with session.no_autoflush:
        story = session.get(Story, story_key)
    if story is None or inspect(story).deleted:
        return None
    return story


def _resolve_story_articles(session: ORMSession, story_key: str) -> dict[str, StoryArticle]:
    with session.no_autoflush:
        story_articles = {
            story_article.article_id: story_article
            for story_article in session.scalars(
                select(StoryArticle).where(StoryArticle.story_key == story_key)
            ).all()
        }

    for instance in _iter_session_objects(session):
        if not isinstance(instance, StoryArticle) or instance.story_key != story_key:
            continue
        if inspect(instance).deleted:
            story_articles.pop(instance.article_id, None)
            continue
        story_articles[instance.article_id] = instance

    return story_articles


def _iter_session_objects(session: ORMSession) -> list[object]:
    seen: set[int] = set()
    instances: list[object] = []
    for collection in (session.identity_map.values(), session.new, session.deleted):
        for instance in collection:
            identity = id(instance)
            if identity in seen:
                continue
            seen.add(identity)
            instances.append(instance)
    return instances
