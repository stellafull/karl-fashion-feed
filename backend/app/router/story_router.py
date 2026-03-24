"""Read APIs for persisted stories."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models.article import Article
from backend.app.models.story import Story, StoryArticle
from backend.app.schemas.llm.story_taxonomy import ALLOWED_STORY_CATEGORIES
from backend.app.schemas.story_feed import (
    StoryFeedCategory,
    StoryFeedMeta,
    StoryFeedResponse,
    StoryFeedSource,
    StoryFeedTopic,
)

router = APIRouter(prefix="/stories", tags=["stories"])


@dataclass(frozen=True)
class StoryArticleRow:
    """Joined story/article row used to build the frontend feed."""

    story_key: str
    story_title: str
    story_summary: str
    story_key_points: list[str]
    story_tags: list[str]
    story_category: str
    story_hero_image_url: str | None
    story_created_at: datetime
    article_rank: int
    article_id: str
    article_source_name: str
    article_source_lang: str
    article_title: str
    article_link: str
    article_published_at: datetime | None


@router.get("/feed", response_model=StoryFeedResponse)
async def get_story_feed(db: Session = Depends(get_db)) -> StoryFeedResponse:
    """Return the persisted story feed consumed by the frontend."""
    story_rows = _load_story_rows(db)
    return _build_story_feed_response(story_rows)


def _load_story_rows(db: Session) -> list[StoryArticleRow]:
    statement: Select = (
        select(
            Story.story_key,
            Story.title_zh.label("story_title_zh"),
            Story.summary_zh.label("story_summary_zh"),
            Story.key_points_json.label("story_key_points_json"),
            Story.tags_json.label("story_tags_json"),
            Story.category.label("story_category"),
            Story.hero_image_url.label("story_hero_image_url"),
            Story.created_at.label("story_created_at"),
            StoryArticle.rank.label("article_rank"),
            Article.article_id.label("article_id"),
            Article.source_name.label("article_source_name"),
            Article.source_lang.label("article_source_lang"),
            Article.title_zh.label("article_title_zh"),
            Article.title_raw.label("article_title_raw"),
            Article.canonical_url.label("article_canonical_url"),
            Article.original_url.label("article_original_url"),
            Article.published_at.label("article_published_at"),
        )
        .join(StoryArticle, StoryArticle.story_key == Story.story_key)
        .join(Article, Article.article_id == StoryArticle.article_id)
        .where(Article.should_publish.is_(True))
        .order_by(Story.created_at.desc(), StoryArticle.rank.asc(), Article.published_at.desc())
    )

    rows = db.execute(statement).all()
    story_rows: list[StoryArticleRow] = []
    for row in rows:
        article_title = (row.article_title_zh or row.article_title_raw or "").strip()
        article_link = (row.article_canonical_url or row.article_original_url or "").strip()
        if not article_title or not article_link:
            continue

        story_rows.append(
            StoryArticleRow(
                story_key=row.story_key,
                story_title=(row.story_title_zh or "").strip(),
                story_summary=(row.story_summary_zh or "").strip(),
                story_key_points=_normalize_string_list(row.story_key_points_json),
                story_tags=_normalize_string_list(row.story_tags_json),
                story_category=(row.story_category or "").strip(),
                story_hero_image_url=(row.story_hero_image_url or "").strip() or None,
                story_created_at=row.story_created_at,
                article_rank=int(row.article_rank),
                article_id=row.article_id,
                article_source_name=(row.article_source_name or "").strip(),
                article_source_lang=(row.article_source_lang or "").strip(),
                article_title=article_title,
                article_link=article_link,
                article_published_at=row.article_published_at,
            )
        )

    return story_rows


def _build_story_feed_response(story_rows: list[StoryArticleRow]) -> StoryFeedResponse:
    grouped_rows: dict[str, list[StoryArticleRow]] = defaultdict(list)
    for row in story_rows:
        grouped_rows[row.story_key].append(row)

    topics: list[StoryFeedTopic] = []
    category_counts: Counter[str] = Counter()
    source_names: set[str] = set()
    article_ids: set[str] = set()
    latest_story_created_at: datetime | None = None

    for story_key, rows in grouped_rows.items():
        sorted_rows = sorted(
            rows,
            key=lambda row: (
                row.article_rank,
                -(row.article_published_at.timestamp() if row.article_published_at else 0),
            ),
        )
        lead_row = sorted_rows[0]
        published_at = _resolve_story_published_at(sorted_rows)
        topics.append(
            StoryFeedTopic(
                id=story_key,
                title=lead_row.story_title,
                summary=lead_row.story_summary,
                key_points=lead_row.story_key_points,
                tags=lead_row.story_tags,
                category=lead_row.story_category,
                category_name=lead_row.story_category,
                image=lead_row.story_hero_image_url or "",
                published=_serialize_datetime(published_at),
                sources=[
                    StoryFeedSource(
                        name=row.article_source_name,
                        title=row.article_title,
                        link=row.article_link,
                        lang=row.article_source_lang,
                    )
                    for row in sorted_rows
                ],
                article_count=len({row.article_id for row in sorted_rows}),
            )
        )
        category_counts[lead_row.story_category] += 1
        source_names.update(
            row.article_source_name for row in sorted_rows if row.article_source_name
        )
        article_ids.update(row.article_id for row in sorted_rows)
        latest_story_created_at = _max_datetime(
            latest_story_created_at,
            lead_row.story_created_at,
        )

    topics.sort(key=lambda topic: topic.published, reverse=True)
    ordered_categories = [
        category for category in ALLOWED_STORY_CATEGORIES if category_counts[category] > 0
    ]
    ordered_categories.extend(
        category
        for category, _count in sorted(
            category_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
        if category not in ALLOWED_STORY_CATEGORIES
    )
    categories = [
        StoryFeedCategory(id="all", name="全部"),
        *[
            StoryFeedCategory(id=category, name=category)
            for category in ordered_categories
        ],
    ]

    generated_at = latest_story_created_at or datetime.utcnow()
    return StoryFeedResponse(
        meta=StoryFeedMeta(
            generated_at=_serialize_datetime(generated_at),
            total_topics=len(topics),
            total_articles=len(article_ids),
            sources_count=len(source_names),
            sources=sorted(source_names),
        ),
        categories=categories,
        topics=topics,
    )


def _normalize_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _resolve_story_published_at(rows: list[StoryArticleRow]) -> datetime:
    published_candidates = [row.article_published_at for row in rows if row.article_published_at]
    if published_candidates:
        return max(published_candidates)
    return rows[0].story_created_at


def _serialize_datetime(value: datetime) -> str:
    return value.isoformat()


def _max_datetime(current: datetime | None, candidate: datetime | None) -> datetime | None:
    if candidate is None:
        return current
    if current is None or candidate > current:
        return candidate
    return current
