"""Business-day helpers for Beijing-time ingestion boundaries."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

BUSINESS_DAY_ZONE = ZoneInfo("Asia/Shanghai")


def business_day_for_ingested_at(value: datetime) -> date:
    """Project an ingestion timestamp into the Asia/Shanghai business day."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(BUSINESS_DAY_ZONE).date()
