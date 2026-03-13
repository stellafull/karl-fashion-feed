from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from backend.app.config.source_config import load_source_configs


class SourceConfigTest(unittest.TestCase):
    def test_load_source_configs_supports_rss_and_web(self) -> None:
        raw = textwrap.dedent(
            """
            - name: Vogue
              type: rss
              feed_url: https://example.com/feed.xml
              lang: en
              category: 高端时装
              enabled: true
            - name: Complex Style
              type: web
              lang: en
              category: 潮流街头
              enabled: true
              start_urls:
                - https://example.com/style
              allowed_domains:
                - example.com
              discovery:
                link_selectors:
                  - a[href]
            """
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "sources.yaml"
            path.write_text(raw, encoding="utf-8")
            sources = load_source_configs(path)

        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0].type, "rss")
        self.assertEqual(sources[0].feed_url, "https://example.com/feed.xml")
        self.assertEqual(sources[1].type, "web")
        self.assertEqual(sources[1].start_urls, ("https://example.com/style",))

    def test_load_source_configs_rejects_invalid_web_source(self) -> None:
        raw = textwrap.dedent(
            """
            - name: Broken Source
              type: web
              lang: en
              category: 高端时装
            """
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "sources.yaml"
            path.write_text(raw, encoding="utf-8")
            with self.assertRaises(ValueError):
                load_source_configs(path)


if __name__ == "__main__":
    unittest.main()
