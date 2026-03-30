from __future__ import annotations

import unittest

from backend.app.config.celery_config import build_celery_settings


class CeleryConfigTest(unittest.TestCase):
    def test_aggregation_task_routes_use_story_clustering_task_name(self) -> None:
        settings = build_celery_settings()
        task_routes = settings["task_routes"]

        self.assertIn("aggregation.cluster_stories_for_day", task_routes)
        self.assertNotIn("aggregation.pack_strict_stories_for_day", task_routes)
        self.assertEqual(
            {"queue": "aggregation"},
            task_routes["aggregation.cluster_stories_for_day"],
        )

