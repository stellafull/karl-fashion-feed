"""Shared environment bootstrap for configuration modules."""

from __future__ import annotations

import os

from dotenv import find_dotenv, load_dotenv

_ = load_dotenv(find_dotenv())


def get_env(name: str, default: str | None = None) -> str | None:
    """Read an environment variable after dotenv bootstrap."""
    return os.getenv(name, default)
