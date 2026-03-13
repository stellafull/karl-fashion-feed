"""Storage configuration for article markdown files."""

from __future__ import annotations

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ARTICLE_MARKDOWN_ROOT = REPO_ROOT / "data" / "articles"
ARTICLE_MARKDOWN_ROOT = Path(
    os.getenv("ARTICLE_MARKDOWN_ROOT", str(DEFAULT_ARTICLE_MARKDOWN_ROOT))
).resolve()
