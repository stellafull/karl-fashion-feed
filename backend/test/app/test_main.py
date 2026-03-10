import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.app import main


class MainEntrypointTests(unittest.TestCase):
    def test_application_metadata_is_stable(self):
        self.assertEqual(main.APP_NAME, "fashion-feed-backend")
        self.assertEqual(main.APP_VERSION, "0.1.0")

    def test_create_app_contract(self):
        if main.FastAPI is None:
            with self.assertRaisesRegex(RuntimeError, "fastapi"):
                main.create_app()
            return

        app = main.create_app()

        self.assertEqual(app.title, "Fashion Feed Backend")
        self.assertEqual(app.docs_url, "/api/docs")
        self.assertEqual(app.openapi_url, "/api/openapi.json")
