"""Milvus client helpers."""

from __future__ import annotations

from backend.app.config.milvus import require_milvus_settings

collection_name = "fashion_news"


def get_milvus_client():
    from pymilvus import MilvusClient

    settings = require_milvus_settings()
    client_kwargs = {"uri": settings.uri}
    if settings.token:
        client_kwargs["token"] = settings.token
    return MilvusClient(**client_kwargs)
