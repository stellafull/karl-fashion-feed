from __future__ import annotations

import unittest
from pathlib import Path

from backend.app.config.auth_config import _default_chat_attachment_root


class AuthConfigTest(unittest.TestCase):
    def test_default_chat_attachment_root_points_to_workspace_backend_data(self) -> None:
        expected_path = Path(__file__).resolve().parents[1] / "data" / "chat_attachments"
        self.assertEqual(str(expected_path), _default_chat_attachment_root())


if __name__ == "__main__":
    unittest.main()
