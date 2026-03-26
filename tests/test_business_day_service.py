"""Tests for business-day projection used by normalization stage inputs."""

from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from backend.app.service.business_day_service import business_day_for_ingested_at


class BusinessDayServiceTest(unittest.TestCase):
    """Verify ingested timestamps map to the Asia/Shanghai business day."""

    def test_business_day_uses_asia_shanghai_boundaries(self) -> None:
        value = datetime(2026, 3, 25, 16, 30, tzinfo=UTC)
        self.assertEqual(business_day_for_ingested_at(value), date(2026, 3, 26))

    def test_business_day_normalizes_naive_utc_inputs(self) -> None:
        value = datetime(2026, 3, 25, 16, 30)
        self.assertEqual(business_day_for_ingested_at(value), date(2026, 3, 26))
