"""Shared SQLAlchemy model helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


JSON_PAYLOAD_TYPE = JSON().with_variant(JSONB(), "postgresql")
