"""Article persistence models for metadata, markdown path, and image assets."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Article(Base):
    __tablename__ = "article"

    article_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    source_name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        index=True,
        comment="数据来源",
    )
    source_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="数据来源类型，如：rss、web等",
    )
    source_lang: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        index=True,
        comment="数据来源语言",
    )
    category: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="文章分类",
    )
    canonical_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
        comment="文章唯一URL",
    )
    original_url: Mapped[str] = mapped_column(Text, nullable=False, comment="原始URL")
    title_raw: Mapped[str] = mapped_column(Text, nullable=False, comment="原始标题")
    summary_raw: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment="原始摘要/预览",
    )
    character_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="标题加正文纯文本字符数，用于chunking策略评估",
    )
    markdown_rel_path: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="按日期分层的Markdown相对路径",
    )
    hero_image_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
        comment="主图ID，指向article_image.image_id",
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        comment="发布时间",
    )
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
    )
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    should_publish: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        comment="LLM判断是否适合进入reader feed",
    )
    reject_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="不发布原因",
    )
    title_zh: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="中文标题",
    )
    summary_zh: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="中文摘要",
    )
    tags_json: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)
    brands_json: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)
    category_candidates_json: Mapped[list | None] = mapped_column(
        JSON,
        nullable=True,
        default=list,
    )
    cluster_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="用于聚类的拼接文本",
    )
    enrichment_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        comment="pending/done/failed",
    )
    enriched_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        comment="enrichment完成时间",
    )
    enrichment_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="enrichment失败信息",
    )
    parse_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        comment="pending/done/failed",
    )
    parsed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        comment="正文解析完成时间",
    )
    parse_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="正文解析失败信息",
    )
    parse_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="正文解析尝试次数",
    )

    # Legacy transitional fields retained for backfill compatibility.
    content_raw: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment="旧版全文字段，迁移期间保留",
    )
    image_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="旧版单图字段，迁移期间保留",
    )


class ArticleImage(Base):
    __tablename__ = "article_image"

    image_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    article_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("article.article_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    image_hash: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="inline")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    alt_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    caption_raw: Mapped[str] = mapped_column(Text, nullable=False, default="")
    credit_raw: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    source_selector: Mapped[str] = mapped_column(Text, nullable=False, default="")
    context_snippet: Mapped[str] = mapped_column(Text, nullable=False, default="")
    fetch_status: Mapped[str] = mapped_column(String(32), nullable=False, default="discovered")
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    visual_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    observed_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ocr_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    visible_entities_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    style_signals_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    contextual_interpretation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    analysis_metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


def ensure_article_storage_schema(bind: Engine) -> None:
    """Create new tables and add storage columns needed by the redesign."""

    from backend.app.models.story import PipelineRun, Story, StoryArticle  # noqa: F401

    _recreate_story_read_model_tables(bind)
    Base.metadata.create_all(bind=bind)
    inspector = inspect(bind)
    if "article" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("article")}
    missing_statements = []
    if "markdown_rel_path" not in existing_columns:
        missing_statements.append("ALTER TABLE article ADD COLUMN markdown_rel_path TEXT")
    if "character_count" not in existing_columns:
        missing_statements.append("ALTER TABLE article ADD COLUMN character_count INTEGER")
    if "hero_image_id" not in existing_columns:
        missing_statements.append("ALTER TABLE article ADD COLUMN hero_image_id VARCHAR(36)")
    if "should_publish" not in existing_columns:
        missing_statements.append("ALTER TABLE article ADD COLUMN should_publish BOOLEAN")
    if "reject_reason" not in existing_columns:
        missing_statements.append("ALTER TABLE article ADD COLUMN reject_reason TEXT")
    if "title_zh" not in existing_columns:
        missing_statements.append("ALTER TABLE article ADD COLUMN title_zh TEXT")
    if "summary_zh" not in existing_columns:
        missing_statements.append("ALTER TABLE article ADD COLUMN summary_zh TEXT")
    if "tags_json" not in existing_columns:
        missing_statements.append("ALTER TABLE article ADD COLUMN tags_json JSON")
    if "brands_json" not in existing_columns:
        missing_statements.append("ALTER TABLE article ADD COLUMN brands_json JSON")
    if "category_candidates_json" not in existing_columns:
        missing_statements.append("ALTER TABLE article ADD COLUMN category_candidates_json JSON")
    if "cluster_text" not in existing_columns:
        missing_statements.append("ALTER TABLE article ADD COLUMN cluster_text TEXT")
    if "enrichment_status" not in existing_columns:
        missing_statements.append("ALTER TABLE article ADD COLUMN enrichment_status VARCHAR(32)")
    if "enriched_at" not in existing_columns:
        missing_statements.append("ALTER TABLE article ADD COLUMN enriched_at TIMESTAMP")
    if "enrichment_error" not in existing_columns:
        missing_statements.append("ALTER TABLE article ADD COLUMN enrichment_error TEXT")
    if "parse_status" not in existing_columns:
        missing_statements.append(
            "ALTER TABLE article ADD COLUMN parse_status VARCHAR(32) DEFAULT 'pending' NOT NULL"
        )
    if "parsed_at" not in existing_columns:
        missing_statements.append("ALTER TABLE article ADD COLUMN parsed_at TIMESTAMP")
    if "parse_error" not in existing_columns:
        missing_statements.append("ALTER TABLE article ADD COLUMN parse_error TEXT")
    if "parse_attempts" not in existing_columns:
        missing_statements.append(
            "ALTER TABLE article ADD COLUMN parse_attempts INTEGER DEFAULT 0 NOT NULL"
        )
    if "article_image" in inspector.get_table_names():
        image_columns = {column["name"] for column in inspector.get_columns("article_image")}
        if "image_hash" not in image_columns:
            missing_statements.append("ALTER TABLE article_image ADD COLUMN image_hash VARCHAR(16)")

    _apply_schema_statements(bind, missing_statements)


def _apply_schema_statements(bind: Engine, statements: list[str]) -> None:
    if not statements:
        return

    with bind.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _recreate_story_read_model_tables(bind: Engine) -> None:
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "story" not in tables and "story_article" not in tables:
        return

    story_columns = {column["name"] for column in inspector.get_columns("story")} if "story" in tables else set()
    story_article_columns = (
        {column["name"] for column in inspector.get_columns("story_article")}
        if "story_article" in tables
        else set()
    )

    current_story_columns = {
        "story_key",
        "created_run_id",
        "title_zh",
        "summary_zh",
        "key_points_json",
        "tags_json",
        "category",
        "hero_image_url",
        "source_article_count",
        "created_at",
    }
    current_story_article_columns = {"story_key", "article_id", "rank"}
    legacy_story_columns = {
        "title",
        "summary",
        "key_points",
        "topic_tags",
        "category_id",
        "category_name",
        "cover_image_url",
        "representative_article_id",
        "rank_score",
        "importance_score",
        "freshness_score",
        "article_count",
        "source_count",
        "first_seen_at",
        "last_aggregated_at",
        "newest_published_at",
        "metadata",
    }
    legacy_story_article_columns = {"member_score", "sort_order", "is_representative"}

    should_recreate_story = (
        "story" in tables
        and (
            bool(story_columns & legacy_story_columns)
            or not current_story_columns.issubset(story_columns)
        )
    )
    should_recreate_story_article = (
        "story_article" in tables
        and (
            bool(story_article_columns & legacy_story_article_columns)
            or not current_story_article_columns.issubset(story_article_columns)
        )
    )

    if not should_recreate_story and not should_recreate_story_article:
        return

    with bind.begin() as connection:
        if "story_article" in tables:
            connection.execute(text("DROP TABLE story_article"))
        if "story" in tables:
            connection.execute(text("DROP TABLE story"))
