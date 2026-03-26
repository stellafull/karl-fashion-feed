"""Article persistence model and digest runtime schema bootstrap."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, Integer, JSON, String, Text, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Article(Base):
    """Truth-source article persisted before downstream normalization and digesting."""

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
        comment="按日期分层的原始Markdown相对路径",
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

    parse_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
    )
    parse_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    parse_updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
    )

    normalization_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
    )
    normalization_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    normalization_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalization_updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
    )

    event_frame_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
    )
    event_frame_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    event_frame_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_frame_updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
    )

    title_zh: Mapped[str | None] = mapped_column(Text, nullable=True, comment="中文标题")
    summary_zh: Mapped[str | None] = mapped_column(Text, nullable=True, comment="中文摘要")
    body_zh_rel_path: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="中文规范化正文Markdown相对路径",
    )


def ensure_article_storage_schema(bind: Engine) -> None:
    """Create the digest runtime tables and repair required storage columns."""

    from backend.app.models.digest import Digest, DigestArticle, DigestStrictStory
    from backend.app.models.event_frame import ArticleEventFrame
    from backend.app.models.image import ArticleImage
    from backend.app.models.runtime import PipelineRun, SourceRunState
    from backend.app.models.strict_story import StrictStory, StrictStoryArticle, StrictStoryFrame

    _drop_story_tables(bind)
    Base.metadata.create_all(
        bind=bind,
        tables=[
            Article.__table__,
            ArticleImage.__table__,
            PipelineRun.__table__,
            SourceRunState.__table__,
            ArticleEventFrame.__table__,
            StrictStory.__table__,
            StrictStoryFrame.__table__,
            StrictStoryArticle.__table__,
            Digest.__table__,
            DigestStrictStory.__table__,
            DigestArticle.__table__,
        ],
    )
    _ensure_article_columns(bind)
    _ensure_pipeline_run_columns(bind)


def _apply_schema_statements(bind: Engine, statements: list[str]) -> None:
    if not statements:
        return

    with bind.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _drop_story_tables(bind: Engine) -> None:
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())
    drop_statements: list[str] = []
    if "story_article" in table_names:
        drop_statements.append("DROP TABLE story_article")
    if "story" in table_names:
        drop_statements.append("DROP TABLE story")
    _apply_schema_statements(bind, drop_statements)


def _ensure_article_columns(bind: Engine) -> None:
    inspector = inspect(bind)
    if "article" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("article")}
    statements: list[str] = []

    if "character_count" not in existing_columns:
        statements.append("ALTER TABLE article ADD COLUMN character_count INTEGER")
    if "markdown_rel_path" not in existing_columns:
        statements.append("ALTER TABLE article ADD COLUMN markdown_rel_path TEXT")
    if "hero_image_id" not in existing_columns:
        statements.append("ALTER TABLE article ADD COLUMN hero_image_id VARCHAR(36)")
    if "parse_status" not in existing_columns:
        statements.append(
            "ALTER TABLE article ADD COLUMN parse_status VARCHAR(32) DEFAULT 'pending' NOT NULL"
        )
    if "parse_attempts" not in existing_columns:
        statements.append("ALTER TABLE article ADD COLUMN parse_attempts INTEGER DEFAULT 0 NOT NULL")
    if "parse_error" not in existing_columns:
        statements.append("ALTER TABLE article ADD COLUMN parse_error TEXT")
    if "parse_updated_at" not in existing_columns:
        statements.append(
            "ALTER TABLE article ADD COLUMN parse_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL"
        )
    if "normalization_status" not in existing_columns:
        statements.append(
            "ALTER TABLE article ADD COLUMN normalization_status VARCHAR(32) DEFAULT 'pending' NOT NULL"
        )
    if "normalization_attempts" not in existing_columns:
        statements.append(
            "ALTER TABLE article ADD COLUMN normalization_attempts INTEGER DEFAULT 0 NOT NULL"
        )
    if "normalization_error" not in existing_columns:
        statements.append("ALTER TABLE article ADD COLUMN normalization_error TEXT")
    if "normalization_updated_at" not in existing_columns:
        statements.append(
            "ALTER TABLE article ADD COLUMN normalization_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL"
        )
    if "event_frame_status" not in existing_columns:
        statements.append(
            "ALTER TABLE article ADD COLUMN event_frame_status VARCHAR(32) DEFAULT 'pending' NOT NULL"
        )
    if "event_frame_attempts" not in existing_columns:
        statements.append(
            "ALTER TABLE article ADD COLUMN event_frame_attempts INTEGER DEFAULT 0 NOT NULL"
        )
    if "event_frame_error" not in existing_columns:
        statements.append("ALTER TABLE article ADD COLUMN event_frame_error TEXT")
    if "event_frame_updated_at" not in existing_columns:
        statements.append(
            "ALTER TABLE article ADD COLUMN event_frame_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL"
        )
    if "title_zh" not in existing_columns:
        statements.append("ALTER TABLE article ADD COLUMN title_zh TEXT")
    if "summary_zh" not in existing_columns:
        statements.append("ALTER TABLE article ADD COLUMN summary_zh TEXT")
    if "body_zh_rel_path" not in existing_columns:
        statements.append("ALTER TABLE article ADD COLUMN body_zh_rel_path TEXT")

    _apply_schema_statements(bind, statements)


def _ensure_pipeline_run_columns(bind: Engine) -> None:
    inspector = inspect(bind)
    if "pipeline_run" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("pipeline_run")}
    statements: list[str] = []

    if "business_date" not in existing_columns:
        statements.append("ALTER TABLE pipeline_run ADD COLUMN business_date DATE NOT NULL")
    if "strict_story_status" not in existing_columns:
        statements.append(
            "ALTER TABLE pipeline_run ADD COLUMN strict_story_status VARCHAR(32) DEFAULT 'pending' NOT NULL"
        )
    if "strict_story_attempts" not in existing_columns:
        statements.append(
            "ALTER TABLE pipeline_run ADD COLUMN strict_story_attempts INTEGER DEFAULT 0 NOT NULL"
        )
    if "strict_story_error" not in existing_columns:
        statements.append("ALTER TABLE pipeline_run ADD COLUMN strict_story_error TEXT")
    if "strict_story_updated_at" not in existing_columns:
        statements.append(
            "ALTER TABLE pipeline_run ADD COLUMN strict_story_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL"
        )
    if "digest_status" not in existing_columns:
        statements.append(
            "ALTER TABLE pipeline_run ADD COLUMN digest_status VARCHAR(32) DEFAULT 'pending' NOT NULL"
        )
    if "digest_attempts" not in existing_columns:
        statements.append(
            "ALTER TABLE pipeline_run ADD COLUMN digest_attempts INTEGER DEFAULT 0 NOT NULL"
        )
    if "digest_error" not in existing_columns:
        statements.append("ALTER TABLE pipeline_run ADD COLUMN digest_error TEXT")
    if "digest_updated_at" not in existing_columns:
        statements.append(
            "ALTER TABLE pipeline_run ADD COLUMN digest_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL"
        )

    _apply_schema_statements(bind, statements)
