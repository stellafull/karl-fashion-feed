from __future__ import annotations

import importlib
import os
import unittest
from unittest.mock import patch

from backend.app.config.celery_config import build_celery_settings


class CeleryConfigTest(unittest.TestCase):
    def test_build_celery_broker_url_loads_password_from_dotenv(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            module = importlib.import_module("backend.app.config.celery_config")
            module = importlib.reload(module)

            self.assertEqual(
                module.build_celery_broker_url(),
                "redis://:Karl1234@localhost:6379/0",
            )

    def test_aggregation_task_routes_use_story_clustering_task_name(self) -> None:
        settings = build_celery_settings()
        task_routes = settings["task_routes"]

        self.assertIn("aggregation.cluster_stories_for_day", task_routes)
        self.assertNotIn("aggregation.pack_strict_stories_for_day", task_routes)
        self.assertEqual(
            {"queue": "aggregation"},
            task_routes["aggregation.cluster_stories_for_day"],
        )
