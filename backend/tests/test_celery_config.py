from __future__ import annotations

import importlib
import os
import unittest
from unittest.mock import patch


class CeleryConfigTest(unittest.TestCase):
    def test_build_celery_broker_url_loads_password_from_dotenv(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            module = importlib.import_module("backend.app.config.celery_config")
            module = importlib.reload(module)

            self.assertEqual(
                module.build_celery_broker_url(),
                "redis://:Karl1234@localhost:6379/0",
            )
