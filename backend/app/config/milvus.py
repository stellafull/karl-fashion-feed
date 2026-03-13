"""Milvus connection settings."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv

_ = load_dotenv(find_dotenv())
_BARE_HOST_PORT_PATTERN = re.compile(r"^[A-Za-z0-9.-]+:\d+$")


@dataclass(frozen=True)
class MilvusSettings:
    uri: str
    token: str | None = None


def _normalize_milvus_uri(uri: str) -> str:
    normalized_uri = uri.strip()
    if "://" in normalized_uri or not _BARE_HOST_PORT_PATTERN.fullmatch(normalized_uri):
        return normalized_uri
    return f"http://{normalized_uri}"


def get_milvus_settings() -> MilvusSettings | None:
    uri = os.getenv("MILVUS_URI")
    token = os.getenv("MILVUS_TOKEN")
    if not uri and not token:
        return None
    if not uri:
        raise RuntimeError("MILVUS_URI is not set. Configure Milvus before using retrieval services.")
    return MilvusSettings(uri=_normalize_milvus_uri(uri), token=token)


def require_milvus_settings() -> MilvusSettings:
    settings = get_milvus_settings()
    if settings is None:
        raise RuntimeError("Milvus is not configured. Set MILVUS_URI before using retrieval services.")
    return settings
