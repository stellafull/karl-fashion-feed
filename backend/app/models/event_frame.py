"""Event frame ORM models."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from sqlalchemy import Date, Float, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class ArticleEventFrame(Base):
    """Structured event extracted from one normalized article."""

    __tablename__ = "article_event_frame"

    event_frame_id: Mapped[str] = mapped_column(
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
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    action_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    object_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    place_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    collection_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    season_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    show_context_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    signature_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    extraction_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    extraction_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
    )
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)
