"""Document storage settings."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

_ = load_dotenv(find_dotenv())


DEFAULT_DOCUMENT_MARKDOWN_ROOT = Path(__file__).resolve().parents[2] / ".runtime" / "documents"


def get_document_markdown_root() -> Path:
    """Return the filesystem root used for cleaned markdown documents."""
    configured = os.getenv("DOCUMENT_MARKDOWN_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_DOCUMENT_MARKDOWN_ROOT
