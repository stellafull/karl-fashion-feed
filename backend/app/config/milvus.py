"""Milvus connection settings."""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.config.env import get_env


@dataclass(frozen=True)
class MilvusSettings:
    uri: str
    token: str | None = None


def get_milvus_settings() -> MilvusSettings | None:
    uri = get_env("MILVUS_URI")
    token = get_env("MILVUS_TOKEN")
    if not uri and not token:
        return None
    if not uri:
        raise RuntimeError("MILVUS_URI is not set. Configure Milvus before using retrieval services.")
    return MilvusSettings(uri=uri, token=token)


def require_milvus_settings() -> MilvusSettings:
    settings = get_milvus_settings()
    if settings is None:
        raise RuntimeError("Milvus is not configured. Set MILVUS_URI before using retrieval services.")
    return settings
