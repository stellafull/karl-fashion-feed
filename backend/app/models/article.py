"""Article persistence model and digest runtime schema bootstrap."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, Integer, JSON, String, Text, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base

LEGACY_ARTICLE_COLUMNS = {
    "normalization_status",
    "normalization_attempts",
    "normalization_error",
    "normalization_updated_at",
    "title_zh",
    "summary_zh",
    "body_zh_rel_path",
}


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Article(Base):
    """Truth-source article persisted before downstream event framing and digesting."""

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


def ensure_article_storage_schema(bind: Engine) -> None:
    """Create the digest runtime tables and repair required storage columns."""

    from backend.app.models.digest import Digest, DigestArticle, DigestStrictStory
    from backend.app.models.event_frame import ArticleEventFrame
    from backend.app.models.image import ArticleImage
    from backend.app.models.runtime import PipelineRun, SourceRunState
    from backend.app.models.strict_story import StrictStory, StrictStoryArticle, StrictStoryFrame

    _fail_on_legacy_story_tables(bind)
    _reset_legacy_pipeline_run_table(bind)
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
    _ensure_article_image_columns(bind)
    _ensure_pipeline_run_columns(bind)


def _apply_schema_statements(bind: Engine, statements: list[str]) -> None:
    if not statements:
        return

    with bind.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _fail_on_legacy_story_tables(bind: Engine) -> None:
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())
    if "story" in table_names or "story_article" in table_names:
        raise RuntimeError(
            "story-era tables are still present; reset local runtime DB state before bootstrap"
        )


def _ensure_article_columns(bind: Engine) -> None:
    inspector = inspect(bind)
    if "article" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("article")}
    legacy_columns = sorted(existing_columns & LEGACY_ARTICLE_COLUMNS)
    if legacy_columns:
        raise RuntimeError(
            "article table contains legacy normalization-era columns "
            f"{legacy_columns}; reset local runtime DB state before bootstrap"
        )

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
    _apply_schema_statements(bind, statements)


def _ensure_pipeline_run_columns(bind: Engine) -> None:
    inspector = inspect(bind)
    if "pipeline_run" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("pipeline_run")}
    if "business_date" not in existing_columns:
        raise RuntimeError(
            "pipeline_run is in a legacy story-era shape; reset local runtime DB state before bootstrap"
        )

    statements: list[str] = []

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


def _ensure_article_image_columns(bind: Engine) -> None:
    inspector = inspect(bind)
    if "article_image" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("article_image")}
    statements: list[str] = []
    if "image_hash" not in existing_columns:
        statements.append("ALTER TABLE article_image ADD COLUMN image_hash VARCHAR(16)")
    if "visual_attempts" not in existing_columns:
        statements.append(
            "ALTER TABLE article_image ADD COLUMN visual_attempts INTEGER DEFAULT 0 NOT NULL"
        )

    _apply_schema_statements(bind, statements)


def _reset_legacy_pipeline_run_table(bind: Engine) -> None:
    inspector = inspect(bind)
    if "pipeline_run" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("pipeline_run")}
    if "business_date" in existing_columns:
        return

    with bind.begin() as connection:
        row_count = connection.execute(text("SELECT COUNT(*) FROM pipeline_run")).scalar_one()
        if row_count > 0:
            raise RuntimeError(
                "pipeline_run contains legacy story-era runtime rows; reset local runtime DB state before bootstrap"
            )
        if "source_run_state" in inspector.get_table_names():
            connection.execute(text("DROP TABLE source_run_state"))
        connection.execute(text("DROP TABLE pipeline_run"))
