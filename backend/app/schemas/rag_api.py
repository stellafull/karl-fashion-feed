"""HTTP DTOs and request-scoped context for the RAG answer API."""

from __future__ import annotations

import base64
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.app.schemas.rag_query import ArticlePackage, QueryFilters, QueryPlan


class RequestImageInput(BaseModel):
    """One request-scoped image payload used by LLM perception and retrieval."""

    mime_type: str
    base64_data: str

    @model_validator(mode="after")
    def validate_payload(self) -> "RequestImageInput":
        if not self.mime_type.strip():
            raise ValueError("request image mime_type must not be empty")
        if not self.base64_data.strip():
            raise ValueError("request image base64_data must not be empty")
        return self

    def to_data_url(self) -> str:
        """Return the image as a data URL for multimodal model calls."""
        return f"data:{self.mime_type};base64,{self.base64_data}"

    @classmethod
    def from_bytes(cls, *, mime_type: str, content: bytes) -> "RequestImageInput":
        """Build a request image payload from uploaded bytes."""
        if not content:
            raise ValueError("uploaded image content must not be empty")
        encoded = base64.b64encode(content).decode("ascii")
        return cls(mime_type=mime_type, base64_data=encoded)


class RagQueryRequest(BaseModel):
    """Validated request payload for one RAG answer query."""

    query: str | None = None
    filters: QueryFilters = Field(default_factory=QueryFilters)
    limit: int = Field(default=10, ge=1, le=50)

    @model_validator(mode="after")
    def normalize_query(self) -> "RagQueryRequest":
        if self.query is not None:
            normalized_query = self.query.strip()
            self.query = normalized_query or None
        return self


class RagRequestContext(BaseModel):
    """Request-scoped runtime context injected into retrieval tools."""

    filters: QueryFilters = Field(default_factory=QueryFilters)
    limit: int = Field(default=10, ge=1, le=50)
    request_image: RequestImageInput | None = None

    @property
    def has_request_image(self) -> bool:
        """Return whether the current request includes an uploaded image."""
        return self.request_image is not None


class WebSearchResult(BaseModel):
    """One Brave search result returned to the answer layer."""

    title: str
    url: str
    snippet: str


class AnswerCitation(BaseModel):
    """One stable citation emitted by the answer API."""

    marker: str
    source_type: Literal["rag", "web"]
    title: str | None = None
    source_name: str | None = None
    url: str
    snippet: str | None = None
    article_id: str | None = None
    article_image_id: str | None = None
    chunk_index: int | None = None


class RagAnswerResponse(BaseModel):
    """Final HTTP response returned by the single-entry answer API."""

    answer: str
    citations: list[AnswerCitation] = Field(default_factory=list)
    packages: list[ArticlePackage] = Field(default_factory=list)
    query_plans: list[QueryPlan] = Field(default_factory=list)
    web_results: list[WebSearchResult] = Field(default_factory=list)
