"""Document storage settings."""

from __future__ import annotations

from pathlib import Path

from backend.app.config.env import get_env


DEFAULT_DOCUMENT_MARKDOWN_ROOT = Path(__file__).resolve().parents[2] / ".runtime" / "documents"


def get_document_markdown_root() -> Path:
    """Return the filesystem root used for cleaned markdown documents."""
    configured = get_env("DOCUMENT_MARKDOWN_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_DOCUMENT_MARKDOWN_ROOT
